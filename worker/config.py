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


def _optional_float(name: str) -> float | None:
    """Like _float but returns None when unset — lets the caller/provider decide."""
    val = os.environ.get(name, "")
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        print(f"[TRIM] ERROR: {name}={val!r} is not a valid number. Ignoring.", file=sys.stderr)
        return None


def _optional_int(name: str) -> int | None:
    """Like _int but returns None when unset — used for dynamic-default fields."""
    val = os.environ.get(name, "")
    if not val:
        return None
    try:
        return int(val)
    except ValueError:
        print(f"[TRIM] ERROR: {name}={val!r} is not a valid integer. Ignoring.", file=sys.stderr)
        return None


# ── Provider / model ──────────────────────────────────────────────────────────
# TRIM_ROUTE_TEXT is the primary model for text summarization.
# WORKER_MODEL is a legacy alias: if TRIM_ROUTE_TEXT is set it wins, otherwise
# WORKER_MODEL is used.  Both env vars are accepted for backward compatibility.
TRIM_ROUTE_TEXT: str = (
    os.environ.get("TRIM_ROUTE_TEXT", "")
    or os.environ.get("WORKER_MODEL", "")
)
# Legacy alias: always mirrors TRIM_ROUTE_TEXT so existing code referencing
# WORKER_MODEL continues to work.
WORKER_MODEL: str = TRIM_ROUTE_TEXT

WORKER_TEMPERATURE: float | None = _optional_float("WORKER_TEMPERATURE")

# Optional per-content-type model overrides.
# When unset, routing.py falls back to TRIM_ROUTE_TEXT and checks capability.
TRIM_ROUTE_PDF: str = os.environ.get("TRIM_ROUTE_PDF", "")
TRIM_ROUTE_VISION: str = os.environ.get("TRIM_ROUTE_VISION", "")
# Large-context fallback activated on ContextWindowExceededError.
TRIM_ROUTE_FALLBACK: str = os.environ.get("TRIM_ROUTE_FALLBACK", "")

# ── Routing thresholds ────────────────────────────────────────────────────────
SHUNT_MIN_LINES: int = _int("SHUNT_MIN_LINES", 350)
SHUNT_TIMEOUT_SECONDS: int = _int("SHUNT_TIMEOUT_SECONDS", 45)
# SHUNT_MAX_BYTES is now an *optional* override.  When None (default) the value
# is computed dynamically from the model's context window by routing.py.
# Set the env var explicitly to restore the old static behaviour.
SHUNT_MAX_BYTES: int | None = _optional_int("SHUNT_MAX_BYTES")

# ── Web fetch thresholds ───────────────────────────────────────────────────────
# Minimum response size (bytes) before TRIM intercepts a WebFetch call.
# Responses smaller than this are let through unchanged.
SHUNT_MIN_WEB_BYTES: int = _int("SHUNT_MIN_WEB_BYTES", 10_000)   # 10 KB default
# Maximum response size (bytes) TRIM will attempt to summarise via WebFetch.
# Responses larger than this are passed through (curl --max-filesize enforces this).
SHUNT_MAX_WEB_BYTES: int = _int("SHUNT_MAX_WEB_BYTES", 5_000_000)  # 5 MB default

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

# ── Diff-aware summarization (optional) ───────────────────────────────────────
# Set TRIM_CACHE_FILE to enable caching + delta summarization.
# Leave unset to disable (TRIM behaves as before — full summarization every read).
TRIM_CACHE_FILE: str = os.environ.get("TRIM_CACHE_FILE", "")
# Fraction of file lines that must change before falling back to full re-summarization.
# 0.4 = if more than 40% of the file changed, skip the delta path.
TRIM_DELTA_THRESHOLD: float = _float("TRIM_DELTA_THRESHOLD", 0.4)
# Max number of delta updates before forcing a full re-summarization.
# Prevents compounding inaccuracy from repeated patches on the same summary.
TRIM_MAX_DELTA_COUNT: int = _int("TRIM_MAX_DELTA_COUNT", 5)


# ── Startup validation ────────────────────────────────────────────────────────

def validate() -> None:
    """Check that all required env vars are set. Call once at process startup.

    Prints a clear error to stderr and exits with code 1 if anything is missing,
    rather than failing later with a cryptic LiteLLM error.
    """
    # At least one of TRIM_ROUTE_TEXT or WORKER_MODEL must be set.
    if not TRIM_ROUTE_TEXT:
        print(
            "[TRIM] ERROR: TRIM_ROUTE_TEXT (or legacy WORKER_MODEL) is required but not set. "
            "Add it to your .env file (see .env.example).",
            file=sys.stderr,
        )
        sys.exit(1)
