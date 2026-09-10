"""FastAPI HTTP server — for container / remote-worker mode.

Endpoints:
  GET  /health        → {"status": "ok", "model": "..."}
  GET  /dashboard     → HTML metrics dashboard
  GET  /api/metrics   → raw JSONL records as JSON
  POST /bulk-read     → {"summary": "...", "line_count": N, ...}

Auth (optional):
  Set TRIM_API_KEY env var on the server. Callers must then send:
    X-TRIM-Key: <key>
  Applies to /bulk-read only. /health, /dashboard and /api/metrics are open.
  Leave TRIM_API_KEY unset to disable auth (local/trusted use).

Rate limiting (optional):
  Set SHUNT_RATE_LIMIT_RPM to a positive integer to cap /bulk-read requests
  per minute across the whole server (not per-IP). 0 = disabled (default).
  The hook fails-open on 429, so Claude reads the file normally on limit hit.
"""
from __future__ import annotations

import collections
import secrets
import threading
import time

import litellm.exceptions
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from worker import config, dashboard
from worker.backends.litellm_backend import LiteLLMBackend
from worker.modes.bulk_reader import BulkReaderMode

app = FastAPI(title="trim-worker", version="0.1.0")

# Shared backend (one per process)
_backend = LiteLLMBackend()
_reader = BulkReaderMode(backend=_backend)

# Max content size: slightly above SHUNT_MAX_BYTES to match hook-side guard
_MAX_CONTENT_BYTES = config.SHUNT_MAX_BYTES + 4096


class _SlidingWindowRateLimiter:
    """Thread-safe sliding-window rate limiter (server-wide, not per-IP).

    Disabled when rpm == 0.  The hook fails-open on 429, so hitting the limit
    causes Claude to read the file normally — no disruption to the developer.
    """

    def __init__(self, rpm: int) -> None:
        self._rpm = rpm
        self._window: collections.deque[float] = collections.deque()
        self._lock = threading.Lock()

    def is_allowed(self) -> bool:
        if self._rpm <= 0:
            return True
        now = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            while self._window and self._window[0] < cutoff:
                self._window.popleft()
            if len(self._window) >= self._rpm:
                return False
            self._window.append(now)
            return True


_rate_limiter = _SlidingWindowRateLimiter(config.SHUNT_RATE_LIMIT_RPM)


def _check_auth(request: Request) -> None:
    """Verify X-TRIM-Key header when TRIM_API_KEY is configured."""
    if not config.TRIM_API_KEY:
        return
    provided = request.headers.get("X-TRIM-Key", "")
    if not secrets.compare_digest(provided, config.TRIM_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing X-TRIM-Key")


class BulkReadRequest(BaseModel):
    file_path: str = Field(..., max_length=4096)
    content: str = Field(..., max_length=_MAX_CONTENT_BYTES)
    question: str | None = Field(default=None, max_length=2048)


class BulkReadResponse(BaseModel):
    summary: str
    file_path: str
    line_count: int
    input_tokens: int
    output_tokens: int
    latency_ms: float
    model: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": config.WORKER_MODEL}


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard_view() -> HTMLResponse:
    records = dashboard.read_metrics()
    stats = dashboard.compute_stats(records)
    return HTMLResponse(content=dashboard.render_html(stats))


@app.get("/api/metrics", include_in_schema=False)
def api_metrics() -> JSONResponse:
    records = dashboard.read_metrics()
    return JSONResponse({"records": records, "count": len(records)})


@app.post("/bulk-read", response_model=BulkReadResponse)
def bulk_read(req: BulkReadRequest, request: Request) -> BulkReadResponse:
    _check_auth(request)
    if not _rate_limiter.is_allowed():
        raise HTTPException(status_code=429, detail="Rate limit exceeded — try again shortly")
    try:
        result = _reader.run_from_content(
            file_path=req.file_path,
            content=req.content,
            question=req.question,
            mode="http",
        )
    except litellm.exceptions.RateLimitError as exc:
        # Provider rate-limited us — 429 is semantically correct and easier to
        # distinguish from real 500s in monitoring. Hook fails-open either way.
        raise HTTPException(
            status_code=429, detail="Provider rate limit exceeded — try again shortly"
        ) from exc
    except Exception as exc:
        # Do not expose exc details — LiteLLM errors embed API keys in the message.
        raise HTTPException(status_code=500, detail="Worker error — see server logs") from exc

    return BulkReadResponse(
        summary=result.summary,
        file_path=result.file_path,
        line_count=result.line_count,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=result.latency_ms,
        model=result.model,
    )


def serve() -> None:
    """Start the uvicorn server (called from __main__)."""
    uvicorn.run(
        "worker.server:app",
        host="0.0.0.0",
        port=config.WORKER_PORT,
        log_level="info",
    )
