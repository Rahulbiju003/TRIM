"""Shared fixtures for the TRIM test suite."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from worker.modes.bulk_reader import BulkReadResult


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_result(**kwargs) -> BulkReadResult:
    return BulkReadResult(
        summary=str(kwargs.get("summary", "Module defines Foo class with bar() and baz() methods.")),
        file_path=str(kwargs.get("file_path", "/tmp/example.py")),
        line_count=int(kwargs.get("line_count", 400)),
        input_tokens=int(kwargs.get("input_tokens", 1200)),
        output_tokens=int(kwargs.get("output_tokens", 180)),
        latency_ms=float(kwargs.get("latency_ms", 320.5)),
        model=str(kwargs.get("model", "gpt-4.1-nano")),
    )


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_metrics(tmp_path) -> Path:
    """A temporary JSONL metrics file path (does not yet exist)."""
    return tmp_path / "trim-metrics.jsonl"


@pytest.fixture()
def isolated_metrics(tmp_metrics, monkeypatch):
    """Patch config.SHUNT_METRICS_FILE so metrics writes go to a temp file."""
    monkeypatch.setattr("worker.config.SHUNT_METRICS_FILE", str(tmp_metrics))
    monkeypatch.setattr("worker.metrics.config.SHUNT_METRICS_FILE", str(tmp_metrics))
    return tmp_metrics


@pytest.fixture()
def mock_result() -> BulkReadResult:
    """A canned BulkReadResult for unit tests."""
    return _make_result()


@pytest.fixture()
def large_file(tmp_path) -> Path:
    """A text file with 400 lines (above the default 350-line threshold)."""
    f = tmp_path / "large.py"
    f.write_text("\n".join(f"# line {i}" for i in range(400)) + "\n")
    return f


@pytest.fixture()
def small_file(tmp_path) -> Path:
    """A text file with 50 lines (below the default 350-line threshold)."""
    f = tmp_path / "small.py"
    f.write_text("\n".join(f"# line {i}" for i in range(50)) + "\n")
    return f


@pytest.fixture()
def sample_records() -> list[dict]:
    """Realistic metric records for dashboard tests."""
    now = time.time()
    return [
        {
            "ts": now - 3600,
            "file": "/src/app.py",
            "lines": 450,
            "latency_ms": 280.0,
            "input_tokens": 1100,
            "output_tokens": 150,
            "mode": "subprocess",
            "model": "gpt-4.1-nano",
        },
        {
            "ts": now - 1800,
            "file": "/src/models.py",
            "lines": 600,
            "latency_ms": 410.0,
            "input_tokens": 1800,
            "output_tokens": 210,
            "mode": "http",
            "model": "gpt-4.1-nano",
        },
        {
            "ts": now - 900,
            "file": "/src/utils.py",
            "lines": 380,
            "latency_ms": 190.0,
            "input_tokens": 900,
            "output_tokens": 120,
            "mode": "subprocess",
            "model": "gpt-4o-mini",
        },
    ]


@pytest.fixture()
def client(mock_result):
    """FastAPI TestClient with the bulk-read endpoint mocked (no real LLM)."""
    # Import here to avoid side-effects at collection time
    import worker.server as server_module

    mock_reader = MagicMock()
    mock_reader.run_from_content.return_value = mock_result

    with patch.object(server_module, "_reader", mock_reader):
        with TestClient(server_module.app, raise_server_exceptions=False) as c:
            yield c


@pytest.fixture()
def auth_client(mock_result, monkeypatch):
    """TestClient with TRIM_API_KEY set to 'test-secret'."""
    import worker.server as server_module
    import worker.config as config_module

    monkeypatch.setattr(config_module, "TRIM_API_KEY", "test-secret")
    monkeypatch.setattr(server_module.config, "TRIM_API_KEY", "test-secret")

    mock_reader = MagicMock()
    mock_reader.run_from_content.return_value = mock_result

    with patch.object(server_module, "_reader", mock_reader):
        with TestClient(server_module.app, raise_server_exceptions=False) as c:
            yield c
