"""PDF handler — Tier 1 (native multimodal) + Tier 2 (text extraction via pymupdf)."""
from __future__ import annotations
import base64

MULTIMODAL_PROMPT = (
    "Summarise this PDF: its purpose, main sections, key findings, "
    "tables, and any important data. Be concise."
)


def build_multimodal_parts(raw_bytes: bytes) -> list:
    """Build LiteLLM message parts for native PDF sending."""
    b64 = base64.b64encode(raw_bytes).decode()
    return [
        {"type": "text", "text": MULTIMODAL_PROMPT},
        {
            "type": "image_url",
            "image_url": {"url": f"data:application/pdf;base64,{b64}"},
        },
    ]


def extract_text(raw_bytes: bytes) -> str | None:
    """Extract text from PDF using pymupdf (Tier 2 fallback)."""
    try:
        import fitz  # type: ignore[import-untyped]  # pymupdf
        import io
        doc = fitz.open(stream=io.BytesIO(raw_bytes), filetype="pdf")
        pages = []
        for i, page in enumerate(doc):
            text = page.get_text().strip()
            if text:
                pages.append(f"--- Page {i + 1} ---\n{text}")
        doc.close()
        return "\n\n".join(pages) if pages else None
    except ImportError:
        return None
    except Exception:
        return None
