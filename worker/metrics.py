"""Append-only JSONL metrics writer (fire-and-forget)."""
from __future__ import annotations

import json
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
        }
        path = Path(config.SHUNT_METRICS_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass  # metrics must never crash the main path
