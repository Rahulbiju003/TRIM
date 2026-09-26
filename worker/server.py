"""FastAPI HTTP server — for container / remote-worker mode.

Endpoints:
  GET  /health        → {"status": "ok", "model": "..."}
  GET  /dashboard     → HTML metrics dashboard
  GET  /api/metrics   → raw JSONL records as JSON
  POST /bulk-read     → {"summary": "...", "line_count": N, ...}

Auth (optional):
  Set TRIM_API_KEY env var on the server. Callers must then send:
    X-TRIM-Key: <key>
  Applies to /bulk-read, /web-read and /api/metrics. /health and /dashboard are open.
  Leave TRIM_API_KEY unset to disable auth (local/trusted use).

Rate limiting (optional):
  Set SHUNT_RATE_LIMIT_RPM to a positive integer to cap /bulk-read requests
  per minute across the whole server (not per-IP). 0 = disabled (default).
  The hook fails-open on 429, so Claude reads the file normally on limit hit.
"""
from __future__ import annotations

import base64
import collections
import secrets
import threading
import time

import litellm.exceptions
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from worker import binary, config, dashboard, routing
from worker.backends.litellm_backend import LiteLLMBackend
from worker.modes.bulk_reader import BulkReaderMode, SYSTEM_PROMPT
from worker.web_reader import WebReaderMode

app = FastAPI(title="trim-worker", version="0.1.0")

# Shared backend (one per process)
_backend = LiteLLMBackend()
_reader = BulkReaderMode(backend=_backend)
_web_reader = WebReaderMode(backend=_backend)

# Max content size: slightly above compute_max_bytes to match hook-side guard.
# Computed once at module load time from the primary text model.
_MAX_CONTENT_BYTES = routing.compute_max_bytes(config.TRIM_ROUTE_TEXT) + 4096


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
    content: str | None = Field(default=None, max_length=_MAX_CONTENT_BYTES)
    content_b64: str | None = Field(default=None)
    is_binary: bool = Field(default=False)
    question: str | None = Field(default=None, max_length=2048)


class BulkReadResponse(BaseModel):
    summary: str
    file_path: str
    line_count: int
    input_tokens: int
    output_tokens: int
    latency_ms: float
    model: str
    pass_through: bool = False


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": config.TRIM_ROUTE_TEXT}


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard_view() -> HTMLResponse:
    records = dashboard.read_metrics()
    stats = dashboard.compute_stats(records)
    return HTMLResponse(content=dashboard.render_html(stats))


@app.get("/api/metrics", include_in_schema=False)
async def api_metrics(request: Request) -> JSONResponse:
    _check_auth(request)
    records = dashboard.read_metrics()
    return JSONResponse({"records": records, "count": len(records)})


@app.post("/bulk-read", response_model=BulkReadResponse)
async def bulk_read(req: BulkReadRequest, request: Request) -> BulkReadResponse:
    _check_auth(request)
    if not _rate_limiter.is_allowed():
        raise HTTPException(status_code=429, detail="Rate limit exceeded — try again shortly")

    try:
        if req.is_binary and req.content_b64:
            return await _handle_binary(req)
        else:
            # Text path — require content field
            if not req.content:
                raise HTTPException(status_code=422, detail="content is required for non-binary requests")
            return await _handle_text(req)

    except HTTPException:
        raise
    except litellm.exceptions.RateLimitError as exc:
        # Provider rate-limited us — 429 is semantically correct and easier to
        # distinguish from real 500s in monitoring. Hook fails-open either way.
        raise HTTPException(
            status_code=429, detail="Provider rate limit exceeded — try again shortly"
        ) from exc
    except Exception as exc:
        # Do not expose exc details — LiteLLM errors embed API keys in the message.
        raise HTTPException(status_code=500, detail="Worker error — see server logs") from exc


async def _handle_text(req: BulkReadRequest) -> BulkReadResponse:
    """Process a plain-text file request."""
    assert req.content is not None
    result = await _reader.run_from_content(
        file_path=req.file_path,
        content=req.content,
        question=req.question,
        mode="http",
    )
    return BulkReadResponse(
        summary=result.summary,
        file_path=result.file_path,
        line_count=result.line_count,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=result.latency_ms,
        model=result.model,
        pass_through=False,
    )


async def _handle_binary(req: BulkReadRequest) -> BulkReadResponse:
    """Process a binary file request using the two-tier binary pipeline."""
    assert req.content_b64 is not None
    try:
        raw_bytes = base64.b64decode(req.content_b64)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid base64 in content_b64") from exc

    pdf_model = routing.model_for_pdf()
    vision_model = routing.model_for_vision()

    text_content, multimodal_parts = binary.process(
        file_path=req.file_path,
        raw_bytes=raw_bytes,
        pdf_model=pdf_model,
        vision_model=vision_model,
    )

    # (None, None) → unsupported binary type, signal pass-through to hook
    if text_content is None and multimodal_parts is None:
        return BulkReadResponse(
            summary="",
            file_path=req.file_path,
            line_count=0,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0.0,
            model=config.TRIM_ROUTE_TEXT,
            pass_through=True,
        )

    t0 = time.monotonic()

    if multimodal_parts is not None:
        # Tier 1: native multimodal — pick the right model
        from worker.binary.detector import BinaryType, detect_type
        ftype = detect_type(req.file_path, raw_bytes[:512])
        if ftype == BinaryType.PDF:
            use_model = pdf_model or config.TRIM_ROUTE_TEXT
        else:
            use_model = vision_model or config.TRIM_ROUTE_TEXT
        completion = await _backend.complete_multimodal(
            system=SYSTEM_PROMPT,
            user_parts=multimodal_parts,
            model=use_model,
        )
    else:
        # Tier 2: text extraction — run through the normal text summarization path
        assert text_content is not None
        result = await _reader.run_from_content(
            file_path=req.file_path,
            content=text_content,
            question=req.question,
            mode="http",
        )
        return BulkReadResponse(
            summary=result.summary,
            file_path=result.file_path,
            line_count=result.line_count,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
            model=result.model,
            pass_through=False,
        )

    latency_ms = (time.monotonic() - t0) * 1000
    return BulkReadResponse(
        summary=completion.content,
        file_path=req.file_path,
        line_count=0,
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
        latency_ms=latency_ms,
        model=completion.model,
        pass_through=False,
    )


class WebReadRequest(BaseModel):
    url: str = Field(..., max_length=2048)
    content: str | None = Field(default=None)          # text content (HTML, JSON, etc.)
    content_b64: str | None = Field(default=None)      # base64 for binary (PDF from URL)
    is_binary: bool = Field(default=False)
    prompt: str | None = Field(default=None, max_length=4096)


class WebReadResponse(BaseModel):
    summary: str
    url: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    model: str
    pass_through: bool = False


@app.post("/web-read", response_model=WebReadResponse)
async def web_read(req: WebReadRequest, request: Request) -> WebReadResponse:
    _check_auth(request)
    if not _rate_limiter.is_allowed():
        raise HTTPException(status_code=429, detail="Rate limit exceeded — try again shortly")

    try:
        if req.is_binary and req.content_b64:
            # Binary URL content — reuse binary pipeline (e.g. PDF fetched from a URL)
            raw_bytes = base64.b64decode(req.content_b64)
            pdf_model = routing.model_for_pdf()
            vision_model = routing.model_for_vision()
            text_content, multimodal_parts = binary.process(
                file_path=req.url,
                raw_bytes=raw_bytes,
                pdf_model=pdf_model,
                vision_model=vision_model,
            )
            if text_content is None and multimodal_parts is None:
                return WebReadResponse(
                    summary="", url=req.url, input_tokens=0, output_tokens=0,
                    latency_ms=0.0, model=config.TRIM_ROUTE_TEXT, pass_through=True,
                )
            t0 = time.monotonic()
            if multimodal_parts is not None:
                use_model = pdf_model or config.TRIM_ROUTE_TEXT
                completion = await _backend.complete_multimodal(
                    system=SYSTEM_PROMPT,
                    user_parts=multimodal_parts,
                    model=use_model,
                )
            else:
                assert text_content is not None
                web_result = await _web_reader.run_from_content(
                    url=req.url, content=text_content, prompt=req.prompt,
                )
                return WebReadResponse(
                    summary=web_result.summary, url=web_result.url,
                    input_tokens=web_result.input_tokens,
                    output_tokens=web_result.output_tokens,
                    latency_ms=web_result.latency_ms, model=web_result.model,
                    pass_through=web_result.pass_through,
                )
            latency_ms = (time.monotonic() - t0) * 1000
            return WebReadResponse(
                summary=completion.content, url=req.url,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                latency_ms=latency_ms, model=completion.model,
            )
        else:
            if not req.content:
                raise HTTPException(status_code=422, detail="content is required for non-binary requests")
            result = await _web_reader.run_from_content(
                url=req.url, content=req.content, prompt=req.prompt,
            )
            return WebReadResponse(
                summary=result.summary, url=result.url,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                latency_ms=result.latency_ms, model=result.model,
                pass_through=result.pass_through,
            )

    except HTTPException:
        raise
    except litellm.exceptions.RateLimitError as exc:
        raise HTTPException(status_code=429, detail="Provider rate limit exceeded — try again shortly") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Worker error — see server logs") from exc


def serve() -> None:
    """Start the uvicorn server (called from __main__)."""
    uvicorn.run(
        "worker.server:app",
        host="0.0.0.0",
        port=config.WORKER_PORT,
        log_level="info",
    )
