"""RTK (Rust Token Killer) integration for TRIM.

RTK is an optional Rust binary that pre-compresses code files using structural
heuristics (strips comments + function bodies, keeps signatures + imports).
When available, TRIM uses it as a zero-cost Tier 0 path before calling the LLM.

If RTK is not installed, this module is a no-op — TRIM behaves as before.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass


_RTK_PATH: str | None = shutil.which("rtk")


@dataclass
class RTKResult:
    content: str
    original_lines: int
    compressed_lines: int

    @property
    def reduction_ratio(self) -> float:
        if self.original_lines == 0:
            return 0.0
        return 1.0 - (self.compressed_lines / self.original_lines)


def is_available() -> bool:
    """True if the `rtk` binary is on PATH."""
    return _RTK_PATH is not None


def compress(file_path: str, original_content: str) -> RTKResult | None:
    """Run `rtk read --filter aggressive` on the file.

    Returns RTKResult on success, None if RTK is unavailable or errors.
    Never raises — always fails gracefully so caller falls through to LLM path.

    In HTTP server mode the original file_path may not exist on this machine.
    We write content to a temp file with the correct extension so RTK can
    detect the language and apply the right heuristics.
    """
    if _RTK_PATH is None:
        return None
    try:
        ext = os.path.splitext(file_path)[1] or ".txt"
        target = file_path if os.path.exists(file_path) else None
        tmp = None
        if target is None:
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=ext, delete=False, encoding="utf-8"
            )
            tmp.write(original_content)
            tmp.close()
            target = tmp.name
        try:
            proc = subprocess.run(
                [_RTK_PATH, "read", "--level=aggressive", target],
                capture_output=True,
                text=True,
                timeout=10,
            )
        finally:
            if tmp is not None:
                os.unlink(tmp.name)
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        compressed = proc.stdout
        return RTKResult(
            content=compressed,
            original_lines=len(original_content.splitlines()),
            compressed_lines=len(compressed.splitlines()),
        )
    except Exception:
        return None
