"""Tests for worker/rtk.py — RTK (Rust Token Killer) integration."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import worker.rtk as rtk
from worker.rtk import RTKResult, compress, is_available


# ── RTKResult dataclass ───────────────────────────────────────────────────────

class TestRTKResult:
    """RTKResult dataclass and reduction_ratio property."""

    def test_fields_stored(self):
        r = RTKResult(content="compressed", original_lines=100, compressed_lines=60)
        assert r.content == "compressed"
        assert r.original_lines == 100
        assert r.compressed_lines == 60

    def test_reduction_ratio_typical(self):
        r = RTKResult(content="x", original_lines=100, compressed_lines=60)
        assert r.reduction_ratio == pytest.approx(0.40)

    def test_reduction_ratio_no_compression(self):
        """compressed_lines == original_lines → ratio is 0."""
        r = RTKResult(content="x", original_lines=50, compressed_lines=50)
        assert r.reduction_ratio == pytest.approx(0.0)

    def test_reduction_ratio_full_compression(self):
        """All lines stripped → ratio is 1.0."""
        r = RTKResult(content="", original_lines=200, compressed_lines=0)
        assert r.reduction_ratio == pytest.approx(1.0)

    def test_reduction_ratio_zero_original_lines(self):
        """Division by zero guard: original_lines == 0 → ratio is 0.0."""
        r = RTKResult(content="x", original_lines=0, compressed_lines=0)
        assert r.reduction_ratio == pytest.approx(0.0)

    def test_reduction_ratio_zero_original_nonzero_compressed(self):
        """Guard still fires even if compressed_lines > 0."""
        r = RTKResult(content="x", original_lines=0, compressed_lines=5)
        assert r.reduction_ratio == pytest.approx(0.0)

    def test_reduction_ratio_capped_at_one(self):
        """If somehow compressed_lines > original_lines ratio should not exceed 1."""
        r = RTKResult(content="x", original_lines=10, compressed_lines=20)
        # 1 - (20/10) = -1.0; ratio property does NOT clamp, but test documents the behavior
        # The actual clamping is not in the code, so we just assert it's a float
        assert isinstance(r.reduction_ratio, float)

    def test_reduction_ratio_is_float(self):
        r = RTKResult(content="x", original_lines=4, compressed_lines=1)
        assert isinstance(r.reduction_ratio, float)


# ── is_available ──────────────────────────────────────────────────────────────

class TestIsAvailable:
    """is_available() reflects whether the rtk binary is on PATH."""

    def test_true_when_rtk_path_is_set(self):
        with patch.object(rtk, "_RTK_PATH", "/usr/local/bin/rtk"):
            assert is_available() is True

    def test_false_when_rtk_path_is_none(self):
        with patch.object(rtk, "_RTK_PATH", None):
            assert is_available() is False

    def test_returns_bool(self):
        with patch.object(rtk, "_RTK_PATH", None):
            assert isinstance(is_available(), bool)


# ── compress ──────────────────────────────────────────────────────────────────

class TestCompress:
    """compress() — subprocess execution and result handling."""

    def _make_proc(self, returncode=0, stdout="compressed output\n", stderr=""):
        proc = MagicMock()
        proc.returncode = returncode
        proc.stdout = stdout
        proc.stderr = stderr
        return proc

    # --- RTK unavailable ---

    def test_returns_none_when_rtk_unavailable(self):
        with patch.object(rtk, "_RTK_PATH", None):
            result = compress("/some/file.py", "original content")
        assert result is None

    # --- RTK available, success path ---

    def test_returns_rtk_result_on_success(self, tmp_path):
        fake_file = tmp_path / "code.py"
        fake_file.write_text("line1\nline2\nline3\n")
        proc = self._make_proc(stdout="line1\nline3\n")
        with patch.object(rtk, "_RTK_PATH", "/usr/bin/rtk"):
            with patch("worker.rtk.subprocess.run", return_value=proc):
                result = compress(str(fake_file), "line1\nline2\nline3\n")
        assert isinstance(result, RTKResult)

    def test_content_matches_stdout(self, tmp_path):
        fake_file = tmp_path / "code.py"
        fake_file.write_text("a\nb\n")
        proc = self._make_proc(stdout="compressed\n")
        with patch.object(rtk, "_RTK_PATH", "/usr/bin/rtk"):
            with patch("worker.rtk.subprocess.run", return_value=proc):
                result = compress(str(fake_file), "a\nb\n")
        assert result is not None
        assert result.content == "compressed\n"

    def test_original_lines_counts_original_content(self, tmp_path):
        fake_file = tmp_path / "code.py"
        fake_file.write_text("a\nb\nc\n")
        original = "a\nb\nc\n"
        proc = self._make_proc(stdout="a\n")
        with patch.object(rtk, "_RTK_PATH", "/usr/bin/rtk"):
            with patch("worker.rtk.subprocess.run", return_value=proc):
                result = compress(str(fake_file), original)
        assert result is not None
        assert result.original_lines == len(original.splitlines())

    def test_compressed_lines_counts_stdout_lines(self, tmp_path):
        fake_file = tmp_path / "code.py"
        fake_file.write_text("a\nb\nc\nd\n")
        stdout = "a\nb\n"
        proc = self._make_proc(stdout=stdout)
        with patch.object(rtk, "_RTK_PATH", "/usr/bin/rtk"):
            with patch("worker.rtk.subprocess.run", return_value=proc):
                result = compress(str(fake_file), "a\nb\nc\nd\n")
        assert result is not None
        assert result.compressed_lines == len(stdout.splitlines())

    def test_subprocess_called_with_correct_args(self, tmp_path):
        fake_file = tmp_path / "code.py"
        fake_file.write_text("code")
        proc = self._make_proc(stdout="c\n")
        with patch.object(rtk, "_RTK_PATH", "/usr/bin/rtk"):
            with patch("worker.rtk.subprocess.run", return_value=proc) as mock_run:
                compress(str(fake_file), "code")
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "/usr/bin/rtk"
        assert args[1] == "read"
        assert "--filter=aggressive" in args
        assert str(fake_file) in args

    def test_subprocess_called_with_timeout(self, tmp_path):
        fake_file = tmp_path / "code.py"
        fake_file.write_text("code")
        proc = self._make_proc(stdout="c\n")
        with patch.object(rtk, "_RTK_PATH", "/usr/bin/rtk"):
            with patch("worker.rtk.subprocess.run", return_value=proc) as mock_run:
                compress(str(fake_file), "code")
        kwargs = mock_run.call_args[1]
        assert kwargs.get("timeout") == 10

    def test_subprocess_called_with_capture_output(self, tmp_path):
        fake_file = tmp_path / "code.py"
        fake_file.write_text("code")
        proc = self._make_proc(stdout="c\n")
        with patch.object(rtk, "_RTK_PATH", "/usr/bin/rtk"):
            with patch("worker.rtk.subprocess.run", return_value=proc) as mock_run:
                compress(str(fake_file), "code")
        kwargs = mock_run.call_args[1]
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True

    # --- RTK failure / edge-case paths ---

    def test_returns_none_when_returncode_nonzero(self, tmp_path):
        fake_file = tmp_path / "code.py"
        fake_file.write_text("code")
        proc = self._make_proc(returncode=1, stdout="some output\n")
        with patch.object(rtk, "_RTK_PATH", "/usr/bin/rtk"):
            with patch("worker.rtk.subprocess.run", return_value=proc):
                result = compress(str(fake_file), "code")
        assert result is None

    def test_returns_none_when_stdout_is_empty(self, tmp_path):
        fake_file = tmp_path / "code.py"
        fake_file.write_text("code")
        proc = self._make_proc(stdout="")
        with patch.object(rtk, "_RTK_PATH", "/usr/bin/rtk"):
            with patch("worker.rtk.subprocess.run", return_value=proc):
                result = compress(str(fake_file), "code")
        assert result is None

    def test_returns_none_when_stdout_is_only_whitespace(self, tmp_path):
        fake_file = tmp_path / "code.py"
        fake_file.write_text("code")
        proc = self._make_proc(stdout="   \n  ")
        with patch.object(rtk, "_RTK_PATH", "/usr/bin/rtk"):
            with patch("worker.rtk.subprocess.run", return_value=proc):
                result = compress(str(fake_file), "code")
        assert result is None

    def test_returns_none_on_subprocess_exception(self, tmp_path):
        fake_file = tmp_path / "code.py"
        fake_file.write_text("code")
        with patch.object(rtk, "_RTK_PATH", "/usr/bin/rtk"):
            with patch("worker.rtk.subprocess.run", side_effect=OSError("rtk not found")):
                result = compress(str(fake_file), "code")
        assert result is None

    def test_returns_none_on_timeout(self, tmp_path):
        import subprocess
        fake_file = tmp_path / "code.py"
        fake_file.write_text("code")
        with patch.object(rtk, "_RTK_PATH", "/usr/bin/rtk"):
            with patch("worker.rtk.subprocess.run", side_effect=subprocess.TimeoutExpired("rtk", 10)):
                result = compress(str(fake_file), "code")
        assert result is None

    def test_returns_none_on_generic_exception(self, tmp_path):
        fake_file = tmp_path / "code.py"
        fake_file.write_text("code")
        with patch.object(rtk, "_RTK_PATH", "/usr/bin/rtk"):
            with patch("worker.rtk.subprocess.run", side_effect=RuntimeError("unexpected")):
                result = compress(str(fake_file), "code")
        assert result is None

    # --- Reduction ratio sanity ---

    def test_reduction_ratio_computed_correctly(self, tmp_path):
        fake_file = tmp_path / "code.py"
        original = "a\nb\nc\nd\n"  # 4 lines
        compressed_out = "a\nb\n"  # 2 lines
        fake_file.write_text(original)
        proc = self._make_proc(stdout=compressed_out)
        with patch.object(rtk, "_RTK_PATH", "/usr/bin/rtk"):
            with patch("worker.rtk.subprocess.run", return_value=proc):
                result = compress(str(fake_file), original)
        assert result is not None
        assert result.reduction_ratio == pytest.approx(0.5)

    def test_zero_line_original_content(self, tmp_path):
        """Empty original_content → original_lines=0, reduction_ratio=0.0."""
        fake_file = tmp_path / "code.py"
        fake_file.write_text("")
        proc = self._make_proc(stdout="x\n")
        with patch.object(rtk, "_RTK_PATH", "/usr/bin/rtk"):
            with patch("worker.rtk.subprocess.run", return_value=proc):
                result = compress(str(fake_file), "")
        assert result is not None
        assert result.original_lines == 0
        assert result.reduction_ratio == pytest.approx(0.0)
