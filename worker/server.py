"""FastAPI HTTP server — for container / remote-worker mode.

Endpoints:
  GET  /health        → {"status": "ok", "model": "..."}
  POST /bulk-read     → {"summary": "...", "line_count": N, ...}
"""
from __future__ import annotations

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from worker import config
from worker.backends.litellm_backend import LiteLLMBackend
from worker.modes.bulk_reader import BulkReaderMode

app = FastAPI(title="shunt-worker", version="0.1.0")

# Shared backend (one per process)
_backend = LiteLLMBackend()
_reader = BulkReaderMode(backend=_backend)


class BulkReadRequest(BaseModel):
    file_path: str
    content: str
    question: str | None = None


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


@app.post("/bulk-read", response_model=BulkReadResponse)
def bulk_read(req: BulkReadRequest) -> BulkReadResponse:
    try:
        result = _reader.run_from_content(
            file_path=req.file_path,
            content=req.content,
            question=req.question,
            mode="http",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

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
