"""Diff computation for diff-aware summarization.

Strategy:
  1. git diff HEAD <file>  — fast, precise, handles staged/unstaged changes
  2. difflib against cached content — fallback when git unavailable or file untracked

DiffResult.changed_ratio: changed lines / total current file lines.
  Caller uses this to decide: delta path (small change) vs full re-summarize (large change).
"""
from __future__ import annotations

import difflib
import subprocess
from dataclasses import dataclass


@dataclass
class DiffResult:
    unified: str          # unified diff string to send to the worker LLM
    changed_ratio: float  # 0.0–1.0 fraction of lines that changed


def compute(
    file_path: str,
    current_content: str,
    cached_content: str,
    timeout: float = 5.0,
) -> DiffResult | None:
    """Return a DiffResult or None if the diff cannot be computed.

    None signals the caller to fall back to full re-summarization.
    """
    unified = _git_diff(file_path, timeout) or _difflib_diff(
        cached_content, current_content, file_path
    )
    if unified is None:
        return None
    ratio = _changed_ratio(unified, current_content)
    return DiffResult(unified=unified, changed_ratio=ratio)


def _git_diff(file_path: str, timeout: float) -> str | None:
    """Unified diff via git. Returns None if git unavailable or file untracked."""
    try:
        # Check whether the file is tracked before running diff
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", file_path],
            capture_output=True,
            timeout=timeout,
        )
        if tracked.returncode != 0:
            return None  # untracked file — no git diff available

        result = subprocess.run(
            ["git", "diff", "HEAD", "--", file_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None
    except Exception:
        return None


def _difflib_diff(old: str, new: str, file_path: str) -> str | None:
    """Pure-Python unified diff — used when git is unavailable or file is untracked."""
    if not old:
        return None
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
    ))
    if not diff:
        return None
    return "".join(diff)


def _changed_ratio(unified: str, current_content: str) -> float:
    """Fraction of current file lines that appear in the diff as additions or deletions."""
    total_lines = len(current_content.splitlines()) or 1
    changed = sum(
        1 for line in unified.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )
    return min(changed / total_lines, 1.0)
