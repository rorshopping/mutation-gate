"""Tests for the on-disk result cache."""

from __future__ import annotations

from pathlib import Path

from mutation_gate import cache


def test_mutant_key_changes_with_source():
    a = cache.mutant_key("x = 1", "m.py", "num_literal")
    b = cache.mutant_key("x = 2", "m.py", "num_literal")
    c = cache.mutant_key("x = 1", "m.py", "comparison")
    assert a != b and a != c


def test_fingerprint_changes_when_file_changes(tmp_path: Path):
    p = tmp_path / "a.py"
    p.write_text("x = 1", encoding="utf-8")
    fp1 = cache.fingerprint(tmp_path, "pytest", 60)
    p.write_text("x = 2", encoding="utf-8")
    fp2 = cache.fingerprint(tmp_path, "pytest", 60)
    assert fp1 != fp2


def test_fingerprint_stable_for_unchanged_project(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 2", encoding="utf-8")
    fp1 = cache.fingerprint(tmp_path, "pytest -q", 60)
    fp2 = cache.fingerprint(tmp_path, "pytest -q", 60)
    assert fp1 == fp2


def test_fingerprint_ignores_venv(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1", encoding="utf-8")
    venv = tmp_path / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "huge.py").write_text("z = 99", encoding="utf-8")
    fp1 = cache.fingerprint(tmp_path, "pytest", 60)
    (venv / "huge.py").write_text("z = 100", encoding="utf-8")
    fp2 = cache.fingerprint(tmp_path, "pytest", 60)
    assert fp1 == fp2


def test_fingerprint_changes_when_fixture_changes(tmp_path: Path):
    (tmp_path / "data.json").write_text('{"a": 1}', encoding="utf-8")
    fp1 = cache.fingerprint(tmp_path, "pytest", 60)
    (tmp_path / "data.json").write_text('{"a": 2}', encoding="utf-8")
    fp2 = cache.fingerprint(tmp_path, "pytest", 60)
    assert fp1 != fp2


def test_save_and_load_roundtrip(tmp_path: Path):
    cf = tmp_path / "sub" / "cache.json"
    cache.save_cache(cf, "fp1", {"k1": {"status": "killed", "duration": 1.0}})
    data = cache.load_cache(cf)
    assert data is not None
    assert data["version"] == 1
    assert data["fingerprint"] == "fp1"
    assert data["results"]["k1"]["status"] == "killed"


def test_load_results_requires_matching_fingerprint(tmp_path: Path):
    cf = tmp_path / "cache.json"
    cache.save_cache(cf, "fpA", {"k1": {"status": "killed"}})
    assert cache.load_results(cf, "fpA") == {"k1": {"status": "killed"}}
    assert cache.load_results(cf, "fpB") == {}


def test_load_cache_missing_file_returns_none(tmp_path: Path):
    assert cache.load_cache(tmp_path / "nope.json") is None


def test_load_cache_corrupt_returns_none(tmp_path: Path):
    cf = tmp_path / "cache.json"
    cf.write_text("not json {{{", encoding="utf-8")
    assert cache.load_cache(cf) is None
