"""Central configuration loaded from environment variables."""
from __future__ import annotations

import os
import sys


def _int(name: str, default: int) -> int:
    val = os.environ.get(name, "")
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        print(f"[TRIM] ERROR: {name}={val!r} is not a valid integer. Using default {default}.", file=sys.stderr)
        return default


def _float(name: str, default: float) -> float:
    val = os.environ.get(name, "")
    if not val:
        return default
    try:
        return float(val)
    except ValueError:
        print(f"[TRIM] ERROR: {name}={val!r} is not a valid number. Using default {default}.", file=sys.stderr)
        return default


# ── Provider / model ──────────────────────────────────────────────────────────
WORKER_MODEL: str = os.environ.get(
    "WORKER_MODEL", "openrouter/nex-agi/nex-n2.5-mini:free"
)
WORKER_TEMPERATURE: float = _float("WORKER_TEMPERATURE", 0.2)

# ── Routing thresholds ────────────────────────────────────────────────────────
SHUNT_MIN_LINES: int = _int("SHUNT_MIN_LINES", 350)
SHUNT_TIMEOUT_SECONDS: int = _int("SHUNT_TIMEOUT_SECONDS", 45)
# macOS default 400 KB; Linux pipes have a smaller limit (~120 KB)
SHUNT_MAX_BYTES: int = _int("SHUNT_MAX_BYTES", 400_000)

# ── Deployment mode ───────────────────────────────────────────────────────────
# Unset → subprocess mode.  Set → HTTP mode (value is the base URL).
WORKER_URL: str = os.environ.get("WORKER_URL", "").rstrip("/")

# ── HTTP server ───────────────────────────────────────────────────────────────
WORKER_PORT: int = _int("WORKER_PORT", 8080)

# ── Auth (optional) ───────────────────────────────────────────────────────────
# Set TRIM_API_KEY on the server to require callers to pass X-TRIM-Key header.
# Leave unset for local/trusted use (no auth check).
TRIM_API_KEY: str = os.environ.get("TRIM_API_KEY", "")

# ── Rate limiting (HTTP mode only) ────────────────────────────────────────────
# Requests per minute cap for /bulk-read across the whole server.
# 0 = disabled (default). Set for shared/team deployments to prevent runaway usage.
# The hook fails-open on 429, so Claude reads the file normally — no disruption.
SHUNT_RATE_LIMIT_RPM: int = _int("SHUNT_RATE_LIMIT_RPM", 0)

# ── Metrics ───────────────────────────────────────────────────────────────────
SHUNT_METRICS_FILE: str = os.environ.get(
    "SHUNT_METRICS_FILE", "/tmp/trim-metrics.jsonl"
)
