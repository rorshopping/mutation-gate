"""Distributed runner — server side (reference implementation).

A stdlib HTTP server that brokers mutant-execution tasks between a
mutation-gate client and any number of mutation-gate workers. State is
in-memory only (restarting loses jobs) — this is the local/demo reference
for the hosted commercial service, and the protocol it speaks is the one
the hosted add-on would expose.

Endpoints:
  POST /v1/jobs            {token, tasks, test_command, timeout} -> {job_id}
  GET  /v1/tasks/next      -> {job_id, idx, task} | 204
  POST /v1/jobs/<id>/results   {token, worker, results} -> {ok}
  GET  /v1/jobs/<id>       -> {done, results, pending}
  GET  /healthz            -> {ok}
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class JobStore:
    """In-memory job registry. Thread-safe; no persistence."""

    def __init__(self, secret: str = ""):
        self.secret = secret
        self._lock = threading.Lock()
        self.jobs: dict[str, dict[str, Any]] = {}

    def create_job(self, tasks: dict[str, dict], test_command: str, timeout: int) -> str:
        job_id = uuid.uuid4().hex[:8]
        with self._lock:
            self.jobs[job_id] = {
                "tasks": dict(tasks),
                "results": {},
                "test_command": test_command,
                "timeout": int(timeout),
                "done": False,
            }
        return job_id

    def next_task(self) -> tuple[str, str, dict] | None:
        """Pop one task from any active job → (job_id, idx, task)."""
        with self._lock:
            for job_id, job in self.jobs.items():
                if job["done"]:
                    continue
                for idx, task in list(job["tasks"].items()):
                    del job["tasks"][idx]
                    task = {
                        **task,
                        "test_command": job["test_command"],
                        "timeout": job["timeout"],
                    }
                    return job_id, idx, task
        return None

    def submit_results(self, job_id: str, results: list[dict]) -> bool:
        with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            for r in results:
                idx = str(r.get("idx"))
                job["results"][idx] = {
                    k: r.get(k)
                    for k in ("status", "exit_code", "duration", "output", "timed_out")
                }
                job["tasks"].pop(idx, None)
            if not job["tasks"]:
                job["done"] = True
            return True

    def snapshot(self, job_id: str) -> dict | None:
        with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            return {
                "done": job["done"],
                "results": dict(job["results"]),
                "pending": len(job["tasks"]),
            }


class _Handler(BaseHTTPRequestHandler):
    store: JobStore  # set by make_server

    def log_message(self, fmt, *args):  # keep the demo server quiet
        pass

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            return {}

    def _send(self, code: int, payload: dict | None = None) -> None:
        body = b""
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _authed(self, body: dict) -> bool:
        return not self.store.secret or body.get("token") == self.store.secret

    def do_POST(self):
        try:
            body = self._read_json()
            if self.path == "/v1/jobs":
                if not self._authed(body):
                    return self._send(401, {"error": "bad token"})
                tasks = body.get("tasks", {})
                if not tasks:
                    return self._send(400, {"error": "no tasks"})
                job_id = self.store.create_job(
                    tasks, body.get("test_command", "pytest"), int(body.get("timeout", 60))
                )
                return self._send(200, {"job_id": job_id})
            m = re.match(r"^/v1/jobs/([^/]+)/results$", self.path)
            if m:
                if not self._authed(body):
                    return self._send(401, {"error": "bad token"})
                ok = self.store.submit_results(m.group(1), body.get("results", []))
                return self._send(200 if ok else 404, {"ok": ok})
            return self._send(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001 - return the error to the caller
            self._send(500, {"error": str(exc)})

    def do_GET(self):
        try:
            if self.path == "/healthz":
                return self._send(200, {"ok": True})
            if self.path == "/v1/tasks/next":
                got = self.store.next_task()
                if got is None:
                    return self._send(204)
                job_id, idx, task = got
                return self._send(200, {"job_id": job_id, "idx": int(idx), "task": task})
            m = re.match(r"^/v1/jobs/([^/]+)$", self.path)
            if m:
                snap = self.store.snapshot(m.group(1))
                if snap is None:
                    return self._send(404, {"error": "no such job"})
                return self._send(200, snap)
            return self._send(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": str(exc)})


def make_server(port: int = 0, secret: str = "") -> ThreadingHTTPServer:
    """Start a dev server on 127.0.0.1; port 0 picks an ephemeral port."""
    store = JobStore(secret)
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    server.store = store
    _Handler.store = store
    return server
