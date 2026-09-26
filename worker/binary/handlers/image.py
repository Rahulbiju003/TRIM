"""Image handler — Tier 1 (native multimodal via vision model)."""
from __future__ import annotations
import base64
from pathlib import Path

_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".tiff": "image/tiff", ".tif": "image/tiff",
}

MULTIMODAL_PROMPT = (
    "Describe this image in detail: what it shows, any text present, "
    "charts/diagrams (with data), and anything notable. Be concise."
)


def build_multimodal_parts(file_path: str, raw_bytes: bytes) -> list:
    ext = Path(file_path).suffix.lower()
    mime = _MIME.get(ext, "image/jpeg")
    b64 = base64.b64encode(raw_bytes).decode()
    return [
        {"type": "text", "text": MULTIMODAL_PROMPT},
        {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        },
    ]
