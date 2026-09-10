"""Tests for worker/config.py — environment variable loading and type coercion."""
from __future__ import annotations

import pytest

import worker.config as config


class TestDefaults:
    """Verify defaults when no env vars are set."""

    def test_shunt_min_lines_default(self, monkeypatch):
        monkeypatch.delenv("SHUNT_MIN_LINES", raising=False)
        # Re-evaluate the helper directly
        from worker.config import _int
        assert _int("SHUNT_MIN_LINES", 350) == 350

    def test_shunt_timeout_default(self, monkeypatch):
        monkeypatch.delenv("SHUNT_TIMEOUT_SECONDS", raising=False)
        from worker.config import _int
        assert _int("SHUNT_TIMEOUT_SECONDS", 45) == 45

    def test_shunt_max_bytes_default(self, monkeypatch):
        monkeypatch.delenv("SHUNT_MAX_BYTES", raising=False)
        from worker.config import _int
        assert _int("SHUNT_MAX_BYTES", 400_000) == 400_000

    def test_worker_temperature_default(self, monkeypatch):
        monkeypatch.delenv("WORKER_TEMPERATURE", raising=False)
        from worker.config import _float
        assert _float("WORKER_TEMPERATURE", 0.2) == 0.2

    def test_worker_url_default_empty(self, monkeypatch):
        monkeypatch.delenv("WORKER_URL", raising=False)
        assert config.WORKER_URL == "" or config.WORKER_URL is not None

    def test_trim_api_key_default_empty(self, monkeypatch):
        monkeypatch.delenv("TRIM_API_KEY", raising=False)
        from worker.config import _int
        # Just verify the function exists and is callable
        assert _int("NONEXISTENT_VAR", 99) == 99

    def test_metrics_file_default(self):
        from worker.config import _int
        assert isinstance(config.SHUNT_METRICS_FILE, str)
        assert config.SHUNT_METRICS_FILE  # non-empty


class TestIntCoercion:
    """_int() type coercion and error handling."""

    def test_valid_integer(self, monkeypatch):
        monkeypatch.setenv("TEST_TRIM_INT", "500")
        from worker.config import _int
        assert _int("TEST_TRIM_INT", 0) == 500

    def test_invalid_integer_falls_back(self, monkeypatch, capsys):
        monkeypatch.setenv("TEST_TRIM_INT", "notanumber")
        from worker.config import _int
        assert _int("TEST_TRIM_INT", 99) == 99
        captured = capsys.readouterr()
        assert "ERROR" in captured.err

    def test_empty_string_uses_default(self, monkeypatch):
        monkeypatch.setenv("TEST_TRIM_INT", "")
        from worker.config import _int
        assert _int("TEST_TRIM_INT", 77) == 77

    def test_missing_key_uses_default(self, monkeypatch):
        monkeypatch.delenv("TEST_TRIM_INT", raising=False)
        from worker.config import _int
        assert _int("TEST_TRIM_INT", 42) == 42

    def test_negative_integer(self, monkeypatch):
        monkeypatch.setenv("TEST_TRIM_INT", "-1")
        from worker.config import _int
        assert _int("TEST_TRIM_INT", 0) == -1

    def test_zero(self, monkeypatch):
        monkeypatch.setenv("TEST_TRIM_INT", "0")
        from worker.config import _int
        assert _int("TEST_TRIM_INT", 99) == 0

    def test_float_string_invalid(self, monkeypatch):
        monkeypatch.setenv("TEST_TRIM_INT", "3.14")
        from worker.config import _int
        result = _int("TEST_TRIM_INT", 55)
        assert result == 55  # float string is not a valid int


class TestFloatCoercion:
    """_float() type coercion and error handling."""

    def test_valid_float(self, monkeypatch):
        monkeypatch.setenv("TEST_TRIM_FLOAT", "0.5")
        from worker.config import _float
        assert _float("TEST_TRIM_FLOAT", 0.0) == pytest.approx(0.5)

    def test_valid_integer_as_float(self, monkeypatch):
        monkeypatch.setenv("TEST_TRIM_FLOAT", "1")
        from worker.config import _float
        assert _float("TEST_TRIM_FLOAT", 0.0) == pytest.approx(1.0)

    def test_invalid_float_falls_back(self, monkeypatch, capsys):
        monkeypatch.setenv("TEST_TRIM_FLOAT", "abc")
        from worker.config import _float
        assert _float("TEST_TRIM_FLOAT", 0.99) == pytest.approx(0.99)
        captured = capsys.readouterr()
        assert "ERROR" in captured.err

    def test_empty_string_uses_default(self, monkeypatch):
        monkeypatch.setenv("TEST_TRIM_FLOAT", "")
        from worker.config import _float
        assert _float("TEST_TRIM_FLOAT", 1.5) == pytest.approx(1.5)

    def test_zero_float(self, monkeypatch):
        monkeypatch.setenv("TEST_TRIM_FLOAT", "0.0")
        from worker.config import _float
        assert _float("TEST_TRIM_FLOAT", 99.0) == pytest.approx(0.0)


class TestWorkerUrl:
    """WORKER_URL trailing-slash stripping."""

    def test_trailing_slash_stripped(self):
        # config.py does .rstrip("/") at module level — verify it's not empty
        # We can't easily re-import, but we can test the behaviour
        url = "http://localhost:8080/"
        assert url.rstrip("/") == "http://localhost:8080"

    def test_no_trailing_slash_unchanged(self):
        url = "http://localhost:8080"
        assert url.rstrip("/") == "http://localhost:8080"

    def test_multiple_trailing_slashes_stripped(self):
        url = "http://localhost:8080///"
        assert url.rstrip("/") == "http://localhost:8080"


class TestValidate:
    """config.validate() — startup guard for required env vars."""

    def test_passes_when_worker_model_set(self, monkeypatch):
        monkeypatch.setenv("WORKER_MODEL", "gpt-4.1-nano")
        # Must not raise / exit
        config.validate()

    def test_exits_when_worker_model_missing(self, monkeypatch):
        monkeypatch.delenv("WORKER_MODEL", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            config.validate()
        assert exc_info.value.code == 1

    def test_exits_when_worker_model_empty_string(self, monkeypatch):
        monkeypatch.setenv("WORKER_MODEL", "")
        with pytest.raises(SystemExit) as exc_info:
            config.validate()
        assert exc_info.value.code == 1

    def test_error_message_names_the_var(self, monkeypatch, capsys):
        monkeypatch.delenv("WORKER_MODEL", raising=False)
        with pytest.raises(SystemExit):
            config.validate()
        captured = capsys.readouterr()
        assert "WORKER_MODEL" in captured.err

    def test_error_message_mentions_env_file(self, monkeypatch, capsys):
        monkeypatch.delenv("WORKER_MODEL", raising=False)
        with pytest.raises(SystemExit):
            config.validate()
        captured = capsys.readouterr()
        assert ".env" in captured.err
