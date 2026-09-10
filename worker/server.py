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
"""
from __future__ import annotations

import secrets

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
    try:
        result = _reader.run_from_content(
            file_path=req.file_path,
            content=req.content,
            question=req.question,
            mode="http",
        )
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
