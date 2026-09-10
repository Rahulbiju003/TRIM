"""Tests for worker/metrics.py — JSONL writer."""
from __future__ import annotations

import json
import stat
import threading
from pathlib import Path

import pytest

import worker.metrics as metrics


def _write_one(isolated_metrics, **kwargs) -> dict:
    """Write one record and return the parsed JSON from disk."""
    metrics.log(
        file_path=str(kwargs.get("file_path", "/src/foo.py")),
        line_count=int(kwargs.get("line_count", 400)),
        latency_ms=float(kwargs.get("latency_ms", 300.0)),
        input_tokens=int(kwargs.get("input_tokens", 1000)),
        output_tokens=int(kwargs.get("output_tokens", 150)),
        mode=str(kwargs.get("mode", "subprocess")),
        model=str(kwargs.get("model", "gpt-4.1-nano")),
    )
    lines = Path(isolated_metrics).read_text().strip().splitlines()
    return json.loads(lines[-1])


class TestLogWrite:
    """Basic write correctness."""

    def test_creates_file(self, isolated_metrics):
        assert not isolated_metrics.exists()
        metrics.log(
            file_path="/x.py", line_count=100, latency_ms=50.0,
            input_tokens=500, output_tokens=80, mode="subprocess", model="gpt-4.1-nano",
        )
        assert isolated_metrics.exists()

    def test_record_fields_present(self, isolated_metrics):
        rec = _write_one(isolated_metrics)
        assert {"ts", "file", "lines", "latency_ms", "input_tokens", "output_tokens", "mode", "model"} <= rec.keys()

    def test_file_path_stored(self, isolated_metrics):
        rec = _write_one(isolated_metrics, file_path="/important/file.py")
        assert rec["file"] == "/important/file.py"

    def test_line_count_stored(self, isolated_metrics):
        rec = _write_one(isolated_metrics, line_count=789)
        assert rec["lines"] == 789

    def test_mode_stored(self, isolated_metrics):
        rec = _write_one(isolated_metrics, mode="http")
        assert rec["mode"] == "http"

    def test_model_stored(self, isolated_metrics):
        rec = _write_one(isolated_metrics, model="gpt-4o-mini")
        assert rec["model"] == "gpt-4o-mini"

    def test_token_counts_stored(self, isolated_metrics):
        rec = _write_one(isolated_metrics, input_tokens=1234, output_tokens=456)
        assert rec["input_tokens"] == 1234
        assert rec["output_tokens"] == 456

    def test_latency_rounded(self, isolated_metrics):
        rec = _write_one(isolated_metrics, latency_ms=123.456789)
        assert rec["latency_ms"] == pytest.approx(123.5, abs=0.1)

    def test_ts_is_recent_unix(self, isolated_metrics):
        import time
        before = time.time() - 1
        rec = _write_one(isolated_metrics)
        after = time.time() + 1
        assert before <= rec["ts"] <= after

    def test_appends_multiple_records(self, isolated_metrics):
        for i in range(5):
            metrics.log(
                file_path=f"/file{i}.py", line_count=400 + i, latency_ms=100.0,
                input_tokens=100, output_tokens=10, mode="subprocess", model="gpt-4.1-nano",
            )
        lines = isolated_metrics.read_text().strip().splitlines()
        assert len(lines) == 5
        for i, line in enumerate(lines):
            rec = json.loads(line)
            assert rec["file"] == f"/file{i}.py"

    def test_each_line_valid_json(self, isolated_metrics):
        for _ in range(3):
            metrics.log(
                file_path="/x.py", line_count=400, latency_ms=50.0,
                input_tokens=100, output_tokens=10, mode="subprocess", model="m",
            )
        for line in isolated_metrics.read_text().strip().splitlines():
            json.loads(line)  # must not raise


class TestFilePermissions:
    """Metrics file is created with restricted permissions."""

    def test_file_permissions_owner_only(self, isolated_metrics):
        metrics.log(
            file_path="/x.py", line_count=400, latency_ms=50.0,
            input_tokens=100, output_tokens=10, mode="subprocess", model="m",
        )
        file_stat = isolated_metrics.stat()
        mode = stat.S_IMODE(file_stat.st_mode)
        # Owner read+write only (0o600); group and other must have no bits set
        assert mode & 0o077 == 0, f"File permissions too open: {oct(mode)}"
        assert mode & 0o600 == 0o600, f"Owner missing rw: {oct(mode)}"


class TestFailOpen:
    """metrics.log() must never raise, even with bad configuration."""

    def test_bad_path_does_not_raise(self, monkeypatch):
        monkeypatch.setattr("worker.metrics.config.SHUNT_METRICS_FILE", "/no/such/directory/x.jsonl")
        metrics.log(
            file_path="/x.py", line_count=1, latency_ms=1.0,
            input_tokens=1, output_tokens=1, mode="subprocess", model="m",
        )  # must not raise

    def test_read_only_path_does_not_raise(self, tmp_path, monkeypatch):
        # Make the directory read-only so the file can't be created
        ro_dir = tmp_path / "readonly"
        ro_dir.mkdir()
        ro_dir.chmod(0o555)
        monkeypatch.setattr(
            "worker.metrics.config.SHUNT_METRICS_FILE", str(ro_dir / "metrics.jsonl")
        )
        try:
            metrics.log(
                file_path="/x.py", line_count=1, latency_ms=1.0,
                input_tokens=1, output_tokens=1, mode="subprocess", model="m",
            )
        finally:
            ro_dir.chmod(0o755)  # restore so tmp cleanup works


class TestConcurrency:
    """Concurrent writes must not corrupt the JSONL file."""

    def test_concurrent_writes_all_valid_json(self, isolated_metrics):
        errors: list[Exception] = []

        def write_records(n: int) -> None:
            for i in range(n):
                try:
                    metrics.log(
                        file_path=f"/thread_{n}_{i}.py",
                        line_count=400,
                        latency_ms=100.0,
                        input_tokens=100,
                        output_tokens=10,
                        mode="subprocess",
                        model="gpt-4.1-nano",
                    )
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=write_records, args=(10,)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors

        lines = isolated_metrics.read_text().strip().splitlines()
        assert len(lines) == 50  # 5 threads × 10 records
        for line in lines:
            json.loads(line)  # each line must be valid JSON
