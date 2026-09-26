"""Dynamic model routing for TRIM.

Selects the best model per content type and computes payload size limits
from the model's actual context window instead of a static config value.
"""
from __future__ import annotations

import litellm
from worker import config

_BYTES_PER_TOKEN = 3.5
_CONTEXT_HEADROOM = 0.75
_MAX_PAYLOAD_BYTES = 10_000_000  # 10 MB absolute ceiling
_FALLBACK_MAX_BYTES = 120_000    # safe floor for unknown models


def _model_info(model: str) -> dict:
    try:
        return dict(litellm.get_model_info(model))
    except Exception:
        return {}


def compute_max_bytes(model: str) -> int:
    """Dynamic file payload ceiling based on the model's context window.

    If SHUNT_MAX_BYTES is explicitly set in the environment, that value wins.
    Otherwise, derive from max_input_tokens with headroom for prompt overhead.
    """
    if config.SHUNT_MAX_BYTES is not None:
        return config.SHUNT_MAX_BYTES
    info = _model_info(model)
    ctx = info.get("max_input_tokens")
    if not ctx:
        return _FALLBACK_MAX_BYTES
    return int(min(ctx * _CONTEXT_HEADROOM * _BYTES_PER_TOKEN, _MAX_PAYLOAD_BYTES))


def model_for_text() -> str:
    return config.TRIM_ROUTE_TEXT


def model_for_pdf() -> str:
    """Best model for PDFs. Empty string if no PDF-capable model is available."""
    candidate = config.TRIM_ROUTE_PDF or config.TRIM_ROUTE_TEXT
    info = _model_info(candidate)
    if info.get("supports_pdf_input"):
        return candidate
    # Primary model doesn't support PDF natively — text extraction fallback will be used
    return ""


def model_for_vision() -> str:
    """Best model for images. Empty string if no vision model is available."""
    candidate = config.TRIM_ROUTE_VISION or config.TRIM_ROUTE_TEXT
    info = _model_info(candidate)
    if info.get("supports_vision"):
        return candidate
    return ""


def fallback_model() -> str:
    return config.TRIM_ROUTE_FALLBACK


def skip_temperature(model: str) -> bool:
    """True if this model should not receive a temperature parameter."""
    try:
        info = litellm.get_model_info(model)
        return bool(info.get("supports_reasoning", False))
    except Exception:
        return True  # unknown → safe default
