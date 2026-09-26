"""Binary type detection — content-first, extension fallback."""
from __future__ import annotations

import enum
from pathlib import Path


class BinaryType(enum.Enum):
    PDF = "pdf"
    IMAGE = "image"
    OFFICE = "office"
    ARCHIVE = "archive"
    DATABASE = "database"
    UNKNOWN = "unknown"


# Magic byte signatures: (offset, bytes) → BinaryType
_MAGIC: list[tuple[int, bytes, BinaryType]] = [
    (0, b"%PDF",           BinaryType.PDF),
    (0, b"\x89PNG\r\n",   BinaryType.IMAGE),
    (0, b"\xff\xd8\xff",  BinaryType.IMAGE),
    (0, b"GIF8",          BinaryType.IMAGE),
    (0, b"RIFF",          BinaryType.IMAGE),   # WEBP only — confirmed in detect_type
    (0, b"BM",            BinaryType.IMAGE),
    (0, b"PK\x03\x04",   BinaryType.ARCHIVE),  # ZIP (and Office Open XML)
    (0, b"\x1f\x8b",     BinaryType.ARCHIVE),  # gzip
    (0, b"BZh",          BinaryType.ARCHIVE),  # bzip2
    (0, b"\xfd7zXZ",     BinaryType.ARCHIVE),  # xz
    (0, b"Rar!",         BinaryType.ARCHIVE),
    (0, b"7z\xbc\xaf",   BinaryType.ARCHIVE),
    (0, b"SQLite format", BinaryType.DATABASE),
]

_OFFICE_EXTS = {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".doc", ".xls", ".ppt"}
_IMAGE_EXTS  = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".svg"}
_ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".bz2", ".xz", ".rar", ".7z", ".tgz"}
_DB_EXTS = {".db", ".sqlite", ".sqlite3"}


def is_binary(data: bytes) -> bool:
    """True if data looks like binary content (null bytes present)."""
    return b"\x00" in data


def detect_type(file_path: str, header: bytes) -> BinaryType:
    """Detect binary file type from magic bytes + extension."""
    # Magic bytes first (reliable)
    for offset, magic, btype in _MAGIC:
        if header[offset:offset + len(magic)] == magic:
            # RIFF is the container for both WEBP (image) and WAV (audio).
            # Only treat RIFF as IMAGE when bytes 8-11 confirm WEBP.
            if magic == b"RIFF" and header[8:12] != b"WEBP":
                continue  # WAV/other RIFF — fall through to extension check
            # ZIP could be Office Open XML — check extension
            if btype == BinaryType.ARCHIVE:
                ext = Path(file_path).suffix.lower()
                if ext in _OFFICE_EXTS:
                    return BinaryType.OFFICE
            return btype

    # Extension fallback
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return BinaryType.PDF
    if ext in _IMAGE_EXTS:
        return BinaryType.IMAGE
    if ext in _OFFICE_EXTS:
        return BinaryType.OFFICE
    if ext in _ARCHIVE_EXTS:
        return BinaryType.ARCHIVE
    if ext in _DB_EXTS:
        return BinaryType.DATABASE

    return BinaryType.UNKNOWN
