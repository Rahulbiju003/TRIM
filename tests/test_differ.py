"""Tests for worker/differ.py — diff computation for diff-aware summarization."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from worker.differ import DiffResult, compute, _git_diff, _difflib_diff, _changed_ratio


# ── DiffResult dataclass ──────────────────────────────────────────────────────

class TestDiffResult:
    """DiffResult dataclass field storage."""

    def test_fields_stored(self):
        dr = DiffResult(unified="--- a\n+++ b\n", changed_ratio=0.25)
        assert dr.unified == "--- a\n+++ b\n"
        assert dr.changed_ratio == pytest.approx(0.25)

    def test_changed_ratio_zero(self):
        dr = DiffResult(unified="diff", changed_ratio=0.0)
        assert dr.changed_ratio == pytest.approx(0.0)

    def test_changed_ratio_one(self):
        dr = DiffResult(unified="diff", changed_ratio=1.0)
        assert dr.changed_ratio == pytest.approx(1.0)


# ── _changed_ratio ────────────────────────────────────────────────────────────

class TestChangedRatio:
    """_changed_ratio() — fraction of changed lines relative to current file."""

    def test_simple_addition(self):
        unified = "+new line\n"
        current = "old line\nnew line\n"
        # 1 '+' line / 2 total lines = 0.5
        assert _changed_ratio(unified, current) == pytest.approx(0.5)

    def test_simple_deletion(self):
        unified = "-old line\n"
        current = "remaining line\n"
        # 1 '-' line / 1 total line = 1.0 (capped)
        assert _changed_ratio(unified, current) == pytest.approx(1.0)

    def test_header_lines_excluded(self):
        """--- and +++ header lines must not be counted as changes."""
        unified = "--- a/file.py\n+++ b/file.py\n+new content\n"
        current = "existing\nnew content\n"
        # Only "+new content" counts as a changed line (not +++ or ---)
        assert _changed_ratio(unified, current) == pytest.approx(0.5)

    def test_mixed_additions_and_deletions(self):
        unified = "--- a\n+++ b\n-removed\n+added\n"
        current = "a\nb\nadded\n"
        # 2 changed lines / 3 total = 0.666...
        assert _changed_ratio(unified, current) == pytest.approx(2 / 3)

    def test_capped_at_one(self):
        """Ratio never exceeds 1.0."""
        unified = "+a\n+b\n+c\n+d\n+e\n"
        current = "a\n"  # only 1 line, 5 additions = would exceed 1.0 uncapped
        assert _changed_ratio(unified, current) == pytest.approx(1.0)

    def test_empty_current_content_uses_floor_of_one(self):
        """Empty file: denominator is treated as 1 to avoid ZeroDivisionError."""
        unified = "+new\n"
        current = ""
        ratio = _changed_ratio(unified, current)
        assert 0.0 <= ratio <= 1.0

    def test_no_changes_in_diff(self):
        unified = "context line\nanother context\n"
        current = "context line\nanother context\n"
        assert _changed_ratio(unified, current) == pytest.approx(0.0)

    def test_returns_float(self):
        result = _changed_ratio("+x\n", "x\n")
        assert isinstance(result, float)


# ── _difflib_diff ─────────────────────────────────────────────────────────────

class TestDifflibDiff:
    """_difflib_diff() — pure-Python unified diff fallback."""

    def test_returns_none_when_old_is_empty(self):
        result = _difflib_diff("", "new content\n", "file.py")
        assert result is None

    def test_returns_none_when_contents_identical(self):
        content = "line1\nline2\n"
        result = _difflib_diff(content, content, "file.py")
        assert result is None

    def test_returns_diff_string_when_content_differs(self):
        old = "line1\nline2\n"
        new = "line1\nline2\nline3\n"
        result = _difflib_diff(old, new, "file.py")
        assert result is not None
        assert isinstance(result, str)

    def test_diff_contains_added_line(self):
        old = "line1\n"
        new = "line1\nline2\n"
        result = _difflib_diff(old, new, "file.py")
        assert result is not None
        assert "+line2" in result

    def test_diff_contains_removed_line(self):
        old = "line1\nline2\n"
        new = "line1\n"
        result = _difflib_diff(old, new, "file.py")
        assert result is not None
        assert "-line2" in result

    def test_diff_uses_file_path_in_header(self):
        old = "a\n"
        new = "b\n"
        result = _difflib_diff(old, new, "myfile.py")
        assert result is not None
        assert "myfile.py" in result

    def test_diff_has_unified_format_headers(self):
        old = "a\n"
        new = "b\n"
        result = _difflib_diff(old, new, "f.py")
        assert result is not None
        assert "---" in result
        assert "+++" in result

    def test_full_replacement(self):
        old = "old content\n"
        new = "new content\n"
        result = _difflib_diff(old, new, "f.py")
        assert result is not None
        assert "+new content" in result
        assert "-old content" in result

    def test_multiline_diff(self):
        old = "a\nb\nc\n"
        new = "a\nx\nc\n"
        result = _difflib_diff(old, new, "f.py")
        assert result is not None
        assert "+x" in result
        assert "-b" in result


# ── _git_diff ─────────────────────────────────────────────────────────────────

class TestGitDiff:
    """_git_diff() — git-based diff with tracked-file check."""

    def _make_proc(self, returncode=0, stdout="", stderr=""):
        proc = MagicMock()
        proc.returncode = returncode
        proc.stdout = stdout
        proc.stderr = stderr
        return proc

    def test_returns_none_when_file_untracked(self):
        """ls-files --error-unmatch returns non-zero → untracked."""
        with patch("worker.differ.subprocess.run", return_value=self._make_proc(returncode=1)):
            result = _git_diff("/untracked/file.py", timeout=5.0)
        assert result is None

    def test_returns_diff_when_tracked_and_changed(self):
        tracked_proc = self._make_proc(returncode=0)
        diff_proc = self._make_proc(returncode=0, stdout="@@ -1 +1 @@\n-old\n+new\n")
        with patch("worker.differ.subprocess.run", side_effect=[tracked_proc, diff_proc]):
            result = _git_diff("/tracked/file.py", timeout=5.0)
        assert result is not None
        assert "-old" in result

    def test_returns_none_when_diff_has_no_changes(self):
        """git diff returns 0 but stdout is empty (no changes)."""
        tracked_proc = self._make_proc(returncode=0)
        diff_proc = self._make_proc(returncode=0, stdout="")
        with patch("worker.differ.subprocess.run", side_effect=[tracked_proc, diff_proc]):
            result = _git_diff("/unchanged/file.py", timeout=5.0)
        assert result is None

    def test_returns_none_when_diff_is_only_whitespace(self):
        tracked_proc = self._make_proc(returncode=0)
        diff_proc = self._make_proc(returncode=0, stdout="   \n")
        with patch("worker.differ.subprocess.run", side_effect=[tracked_proc, diff_proc]):
            result = _git_diff("/file.py", timeout=5.0)
        assert result is None

    def test_returns_none_when_diff_fails(self):
        tracked_proc = self._make_proc(returncode=0)
        diff_proc = self._make_proc(returncode=128, stdout="")
        with patch("worker.differ.subprocess.run", side_effect=[tracked_proc, diff_proc]):
            result = _git_diff("/file.py", timeout=5.0)
        assert result is None

    def test_returns_none_on_subprocess_exception(self):
        with patch("worker.differ.subprocess.run", side_effect=OSError("git not found")):
            result = _git_diff("/file.py", timeout=5.0)
        assert result is None

    def test_returns_none_on_timeout(self):
        import subprocess
        with patch("worker.differ.subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)):
            result = _git_diff("/file.py", timeout=5.0)
        assert result is None

    def test_diff_is_stripped(self):
        """Returned diff string should be strip()ped."""
        tracked_proc = self._make_proc(returncode=0)
        diff_proc = self._make_proc(returncode=0, stdout="  @@ -1 +1 @@\n-old\n+new\n  ")
        with patch("worker.differ.subprocess.run", side_effect=[tracked_proc, diff_proc]):
            result = _git_diff("/file.py", timeout=5.0)
        assert result is not None
        assert not result.startswith(" ")
        assert not result.endswith(" ")

    def test_ls_files_called_first(self):
        """ls-files check must happen before git diff."""
        call_order = []

        def fake_run(args, **kwargs):
            call_order.append(args[1])  # second arg: 'ls-files' or 'diff'
            return self._make_proc(returncode=0, stdout="@@ +1 @@\n+x\n")

        with patch("worker.differ.subprocess.run", side_effect=fake_run):
            _git_diff("/file.py", timeout=5.0)

        assert call_order[0] == "ls-files"
        assert call_order[1] == "diff"


# ── compute (integration of strategies) ──────────────────────────────────────

class TestCompute:
    """compute() — orchestrates git_diff then difflib_diff fallback."""

    def _make_proc(self, returncode=0, stdout="", stderr=""):
        proc = MagicMock()
        proc.returncode = returncode
        proc.stdout = stdout
        proc.stderr = stderr
        return proc

    def test_returns_diff_result_on_success_via_git(self):
        tracked = self._make_proc(returncode=0)
        diff = self._make_proc(returncode=0, stdout="@@ -1 +1 @@\n-old\n+new\n")
        current = "new content\n"
        with patch("worker.differ.subprocess.run", side_effect=[tracked, diff]):
            result = compute("/file.py", current, "old content\n")
        assert isinstance(result, DiffResult)

    def test_returns_diff_result_on_success_via_difflib(self):
        """Git unavailable (subprocess raises) → falls back to difflib."""
        with patch("worker.differ.subprocess.run", side_effect=OSError("no git")):
            result = compute("/file.py", "line1\nline2\n", "line1\n")
        assert isinstance(result, DiffResult)

    def test_returns_none_when_both_strategies_fail(self):
        """Git not tracked AND old content empty → no diff available."""
        with patch("worker.differ.subprocess.run", side_effect=OSError("no git")):
            result = compute("/file.py", "new content\n", "")
        assert result is None

    def test_unified_diff_in_result(self):
        old = "line1\n"
        new = "line1\nline2\n"
        with patch("worker.differ.subprocess.run", side_effect=OSError("no git")):
            result = compute("/file.py", new, old)
        assert result is not None
        assert "+line2" in result.unified

    def test_changed_ratio_in_result(self):
        old = "line1\n"
        new = "line1\nline2\n"
        with patch("worker.differ.subprocess.run", side_effect=OSError("no git")):
            result = compute("/file.py", new, old)
        assert result is not None
        assert 0.0 <= result.changed_ratio <= 1.0

    def test_git_diff_preferred_over_difflib(self):
        """When git diff succeeds it should be used, not difflib."""
        tracked = self._make_proc(returncode=0)
        git_out = "@@ -1 +1 @@\n-x\n+y\n"
        diff = self._make_proc(returncode=0, stdout=git_out)
        with patch("worker.differ.subprocess.run", side_effect=[tracked, diff]):
            result = compute("/file.py", "y\n", "x\n")
        assert result is not None
        assert result.unified == git_out.strip()

    def test_falls_back_to_difflib_when_file_untracked(self):
        """If git says file is untracked, difflib is used instead."""
        # ls-files says not tracked
        untracked = self._make_proc(returncode=1)
        old = "old line\n"
        new = "new line\n"
        with patch("worker.differ.subprocess.run", return_value=untracked):
            result = compute("/file.py", new, old)
        assert result is not None
        assert "+new line" in result.unified

    def test_result_changed_ratio_is_float(self):
        with patch("worker.differ.subprocess.run", side_effect=OSError("no git")):
            result = compute("/f.py", "a\nb\n", "a\n")
        assert result is not None
        assert isinstance(result.changed_ratio, float)

    def test_identical_content_returns_none_via_difflib(self):
        """No changes → difflib returns None → compute returns None."""
        content = "same line\n"
        with patch("worker.differ.subprocess.run", side_effect=OSError("no git")):
            result = compute("/f.py", content, content)
        assert result is None

    def test_timeout_param_passed_to_git(self):
        """The timeout parameter should be forwarded to subprocess calls."""
        collected_timeouts = []

        def fake_run(args, **kwargs):
            collected_timeouts.append(kwargs.get("timeout"))
            return self._make_proc(returncode=1)  # untracked → only one call

        with patch("worker.differ.subprocess.run", side_effect=fake_run):
            compute("/f.py", "new\n", "old\n", timeout=3.5)

        assert collected_timeouts[0] == pytest.approx(3.5)
