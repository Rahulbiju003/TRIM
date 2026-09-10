"""Central configuration loaded from environment variables."""
from __future__ import annotations

import os

# ── Provider / model ──────────────────────────────────────────────────────────
WORKER_MODEL: str = os.environ.get(
    "WORKER_MODEL", "openrouter/google/gemma-3n-e4b-it:free"
)
WORKER_TEMPERATURE: float = float(os.environ.get("WORKER_TEMPERATURE", "0.2"))

# ── Routing thresholds ────────────────────────────────────────────────────────
SHUNT_MIN_LINES: int = int(os.environ.get("SHUNT_MIN_LINES", "350"))
SHUNT_TIMEOUT_SECONDS: int = int(os.environ.get("SHUNT_TIMEOUT_SECONDS", "45"))
# macOS default 400 KB; Linux pipes have a smaller limit (~120 KB)
SHUNT_MAX_BYTES: int = int(os.environ.get("SHUNT_MAX_BYTES", "400000"))

# ── Deployment mode ───────────────────────────────────────────────────────────
# Unset → subprocess mode.  Set → HTTP mode (value is the base URL).
WORKER_URL: str = os.environ.get("WORKER_URL", "").rstrip("/")

# ── HTTP server ───────────────────────────────────────────────────────────────
WORKER_PORT: int = int(os.environ.get("WORKER_PORT", "8080"))

# ── Metrics ───────────────────────────────────────────────────────────────────
SHUNT_METRICS_FILE: str = os.environ.get(
    "SHUNT_METRICS_FILE", "/tmp/shunt-metrics.jsonl"
)
