"""Distributed runner — client and worker side.

Reference implementation of the mutation-gate distributed protocol: a
client serializes mutants into a job on the broker (see server.py), any
number of workers pull tasks and execute them against their own local
checkout of the project, and the client polls until the job completes.

This is the open-source core of the hosted add-on: in cloud mode the
client would target a hosted broker and workers would clone the repo at
a pinned commit instead of running against a local --dir checkout.
"""

from __future__ import annotations

import json
import shlex
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .model import Mutant, MutantResult
from .runner import _ensure_worktree, _run_one, detect_project_python, resolve_test_cmd, subset_prefix


# ---------------------------------------------------------------- http helpers

def _post(url: str, payload: dict, timeout: int = 30) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read() or b"{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read() or b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
        return exc.code, body


def _get(url: str, timeout: int = 30) -> tuple[int, dict]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read() or b"{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        if exc.code == 204:
            return 204, {}
        raw = exc.read() or b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
        return exc.code, body


# ----------------------------------------------------------------- client side

def submit_job(server: str, token: str, tasks: dict[str, dict], test_command: str, timeout: int) -> str:
    status, body = _post(
        f"{server}/v1/jobs",
        {"token": token, "tasks": tasks, "test_command": test_command, "timeout": timeout},
    )
    if status != 200:
        raise RuntimeError(f"submit_job failed ({status}): {body.get('error', body)}")
    return body["job_id"]


def fetch_task(server: str) -> tuple[str, int, dict] | None:
    status, body = _get(f"{server}/v1/tasks/next")
    if status == 204:
        return None
    return body["job_id"], int(body["idx"]), body["task"]


def post_results(server: str, token: str, job_id: str, results: list[dict]) -> None:
    status, body = _post(
        f"{server}/v1/jobs/{job_id}/results", {"token": token, "results": results}
    )
    if status not in (200, 404):
        raise RuntimeError(f"post_results failed ({status}): {body.get('error', body)}")


def poll_job(server: str, job_id: str, total_timeout: float = 3600.0) -> dict:
    deadline = time.monotonic() + total_timeout
    while time.monotonic() < deadline:
        status, body = _get(f"{server}/v1/jobs/{job_id}")
        if status == 404:
            raise RuntimeError(f"job {job_id} not found on {server}")
        if body.get("done"):
            return body["results"]
        time.sleep(0.25)
    raise TimeoutError(f"job {job_id} did not finish within {total_timeout:.0f}s")


# ----------------------------------------------------------------- worker side

def _worker_task(project_dir: Path, pool_dir: Path, task: dict) -> dict:
    """Run one mutant task against a local worktree; returns a result dict."""
    mutant = Mutant(
        id=0,
        file=Path(task["file"]),
        lineno=int(task.get("lineno", 0)),
        operator=task.get("operator", "?"),
        before=task.get("before", ""),
        after=task.get("after", ""),
        source=task["source"],
        original="",
    )
    project_python = detect_project_python(project_dir)
    cmd = resolve_test_cmd(task.get("test_command", "pytest"), project_python)
    subset_files = task.get("subset_files")
    if subset_files:
        cmd = [*subset_prefix(cmd, project_python), *subset_files]
    work = _ensure_worktree(project_dir, pool_dir)
    result = _run_one(work, mutant, cmd, int(task.get("timeout", 60)))
    return {
        "status": result.status,
        "exit_code": result.exit_code,
        "duration": result.duration,
        "output": result.output,
        "timed_out": result.timed_out,
    }


def run_worker_loop(
    server: str,
    token: str,
    project_dir: Path,
    poll_interval: float = 0.5,
    stop: threading.Event | None = None,
) -> None:
    """Block forever pulling tasks from the broker and running them locally."""
    project_dir = Path(project_dir).resolve()
    pool_dir = Path(tempfile.mkdtemp(prefix="mutegate-worker-"))
    print(f"worker: ready, polling {server} (project={project_dir})", file=sys.stderr)
    while True:
        if stop is not None and stop.is_set():
            break
        got = fetch_task(server)
        if got is None:
            time.sleep(poll_interval)
            continue
        job_id, idx, task = got
        start = time.monotonic()
        result = _worker_task(project_dir, pool_dir, task)
        result["idx"] = idx
        result["worker"] = "local"
        result["duration"] = round(time.monotonic() - start, 3)
        post_results(server, token, job_id, [result])
