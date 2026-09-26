"""Append-only JSONL metrics writer (fire-and-forget)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from worker import config


def log(
    *,
    file_path: str,
    line_count: int,
    latency_ms: float,
    input_tokens: int,
    output_tokens: int,
    mode: str,  # "subprocess" | "http"
    model: str,
    cache_hit: bool = False,
    delta: bool = False,
    route: str = "bulk-read",       # "bulk-read" | "web-read"
    content_type: str = "text",     # "text" | "pdf" | "image" | "office" | "archive" | "database"
    rtk_tokens_saved: int = 0,      # tokens saved by RTK pre-compression
    pass_through: bool = False,     # True when no LLM was called
) -> None:
    """Append one metrics record to the JSONL file. Never raises."""
    try:
        record = {
            "ts": time.time(),
            "file": file_path,
            "lines": line_count,
            "latency_ms": round(latency_ms, 1),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "mode": mode,
            "model": model,
            "cache_hit": cache_hit,
            "delta": delta,
            "route": route,
            "content_type": content_type,
            "rtk_tokens_saved": rtk_tokens_saved,
            "pass_through": pass_through,
        }
        path = Path(config.SHUNT_METRICS_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Open with O_CREAT | O_WRONLY | O_APPEND and mode 0o600 (owner read/write only)
        fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(json.dumps(record) + "\n")
        _maybe_rotate(path)
    except Exception:
        pass  # metrics must never crash the main path


_MAX_METRICS_BYTES: int = 10 * 1024 * 1024  # 10 MB (~25 K records)
_KEEP_LINES: int = 10_000


def _maybe_rotate(path: Path) -> None:
    """Keep the metrics file under _MAX_METRICS_BYTES by retaining the last _KEEP_LINES lines."""
    try:
        if path.stat().st_size < _MAX_METRICS_BYTES:
            return
        lines = path.read_text().splitlines()
        if len(lines) <= _KEEP_LINES:
            return
        path.write_text("\n".join(lines[-_KEEP_LINES:]) + "\n")
    except Exception:
        pass
