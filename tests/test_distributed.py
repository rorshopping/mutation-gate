"""Distributed runner tests: broker unit tests + a full local end-to-end."""

import threading
import time
from pathlib import Path

import pytest

from mutation_gate import distributed
from mutation_gate.config import collect_python_files, load_config
from mutation_gate.generate import generate_mutants
from mutation_gate.runner import Runner
from mutation_gate.server import JobStore, make_server

from tests.test_cli import _make_project


# ---------------------------------------------------------------- JobStore unit


def test_job_store_roundtrip():
    store = JobStore()
    job_id = store.create_job(
        {"0": {"file": "src/a.py"}, "1": {"file": "src/b.py"}}, "pytest", 42
    )
    assert store.jobs[job_id]["test_command"] == "pytest"

    got = store.next_task()
    assert got is not None
    job, idx, task = got
    assert job == job_id
    assert task["test_command"] == "pytest"
    assert task["timeout"] == 42
    assert task["file"] in ("src/a.py", "src/b.py")

    got2 = store.next_task()
    assert got2 is not None
    _, idx2, task2 = got2
    assert idx2 != idx and task2["file"] in ("src/a.py", "src/b.py")

    snap = store.snapshot(job_id)
    assert snap["done"] is False
    assert snap["pending"] == 0

    assert store.submit_results(job_id, [{"idx": idx, "status": "killed"}])
    assert store.snapshot(job_id)["done"] is True  # all tasks accounted for
    assert store.snapshot(job_id)["pending"] == 0
    assert store.snapshot(job_id)["results"][str(idx)]["status"] == "killed"

    assert store.next_task() is None  # job done → no more tasks


def test_job_store_token_required():
    store = JobStore(secret="s3cret")
    assert store.next_task() is None  # empty


# ------------------------------------------------------------- HTTP layer unit


def test_http_endpoints_and_auth():
    server = make_server(0, secret="s3cret")
    base = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = distributed._get(f"{base}/healthz")
        assert status == 200 and body == {"ok": True}

        with pytest.raises(RuntimeError):
            distributed.submit_job(base, "wrong", {"0": {}}, "pytest", 10)

        job = distributed.submit_job(base, "s3cret", {"0": {"file": "a.py"}}, "pytest", 10)
        got = distributed.fetch_task(base)
        assert got is not None
        _, idx, task = got
        assert idx == 0 and task["file"] == "a.py"

        status, body = distributed._post(f"{base}/v1/jobs/{job}/results", {"token": "s3cret", "results": [{"idx": idx, "status": "survived"}]})
        assert status == 200 and body == {"ok": True}
        status, body = distributed._get(f"{base}/v1/jobs/{job}")
        assert status == 200 and body["done"] is True
        assert body["results"]["0"]["status"] == "survived"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ----------------------------------------------------------------- end-to-end


def test_distributed_run_matches_local(tmp_path):
    root = _make_project(tmp_path)
    cfg = load_config(root)
    mutants = []
    for f in collect_python_files(root, cfg):
        mutants += generate_mutants(f.read_text(encoding="utf-8"), f.relative_to(root))
    assert mutants, "expected at least one mutant for clamp()"

    runner = Runner(root, test_command="pytest", timeout=60, workers=2)
    local, local_cached = runner.run(mutants)
    assert local_cached == 0

    server = make_server(0, secret="s3cret")
    base = f"http://127.0.0.1:{server.server_address[1]}"
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    stop = threading.Event()
    worker_thread = threading.Thread(
        target=distributed.run_worker_loop,
        args=(base, "s3cret", root),
        kwargs={"poll_interval": 0.05, "stop": stop},
        daemon=True,
    )
    worker_thread.start()
    try:
        remote, remote_cached = runner.run_distributed(base, "s3cret", mutants)
    finally:
        stop.set()
        worker_thread.join(timeout=10)
        server.shutdown()
        server.server_close()

    assert remote_cached == 0
    assert len(local) == len(remote) == len(mutants)
    assert [r.status for r in local] == [r.status for r in remote]
    assert [r.exit_code for r in local] == [r.exit_code for r in remote]


def test_run_distributed_replays_cache(tmp_path):
    root = _make_project(tmp_path)
    cache_file = root / ".mutation-gate" / "cache.json"
    cfg = load_config(root)
    mutants = []
    for f in collect_python_files(root, cfg):
        mutants += generate_mutants(f.read_text(encoding="utf-8"), f.relative_to(root))

    runner = Runner(root, test_command="pytest", timeout=60, workers=2, cache_file=cache_file)
    runner.run(mutants)
    assert cache_file.exists()

    # Second run with no broker should replay everything from cache.
    server = make_server(0, secret="s3cret")
    base = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        results, cached = runner.run_distributed(base, "s3cret", mutants)
        assert cached == len(mutants)
        assert len(results) == len(mutants)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_run_distributed_empty_returns_immediately(tmp_path):
    runner = Runner(tmp_path, test_command="pytest")
    results, cached = runner.run_distributed("http://127.0.0.1:9", "x", [])
    assert results == [] and cached == 0
