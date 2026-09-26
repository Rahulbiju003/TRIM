"""Binary file processing for TRIM.

Tier 1: Send file natively to multimodal LLM (PDF, images) if model supports it.
Tier 2: Extract text content (Office docs, archives, SQLite) and summarize as text.
Pass-through: File type not supported — return None so caller falls back to Claude.
"""
from __future__ import annotations

from worker.binary.detector import BinaryType, detect_type
from worker.binary.handlers import pdf as _pdf
from worker.binary.handlers import image as _image
from worker.binary.handlers import office as _office
from worker.binary.handlers import archive as _archive
from worker.binary.handlers import database as _database


def process(
    file_path: str,
    raw_bytes: bytes,
    pdf_model: str,
    vision_model: str,
) -> tuple[str | None, list | None]:
    """Process a binary file.

    Returns (text_content, multimodal_parts) where:
    - (text_content, None)  → Tier 2: extracted text, use normal complete()
    - (None, parts)         → Tier 1: multimodal parts, use complete_multimodal()
    - (None, None)          → unsupported, pass through

    pdf_model / vision_model: model strings (empty means not available).
    """
    ftype = detect_type(file_path, raw_bytes)

    if ftype == BinaryType.PDF:
        if pdf_model:
            parts = _pdf.build_multimodal_parts(raw_bytes)
            return None, parts
        else:
            text = _pdf.extract_text(raw_bytes)
            return (text, None) if text else (None, None)

    elif ftype == BinaryType.IMAGE:
        if vision_model:
            parts = _image.build_multimodal_parts(file_path, raw_bytes)
            return None, parts
        else:
            return None, None  # no vision model, pass through

    elif ftype == BinaryType.OFFICE:
        text = _office.extract_text(file_path, raw_bytes)
        return (text, None) if text else (None, None)

    elif ftype == BinaryType.ARCHIVE:
        text = _archive.extract_listing(file_path, raw_bytes)
        return (text, None) if text else (None, None)

    elif ftype == BinaryType.DATABASE:
        text = _database.extract_schema(raw_bytes)
        return (text, None) if text else (None, None)

    return None, None  # unsupported → pass through
