"""Archive handler — Tier 2: file listing + text file extraction."""
from __future__ import annotations
import io
import zipfile
import tarfile
import gzip
from pathlib import Path

_TEXT_EXTS = {
    ".py", ".js", ".ts", ".java", ".go", ".rb", ".rs", ".c", ".cpp", ".h",
    ".hpp", ".cs", ".php", ".swift", ".kt", ".sh", ".bash", ".md", ".txt",
    ".json", ".yaml", ".yml", ".toml", ".xml", ".html", ".css", ".sql",
    ".env", ".cfg", ".ini", ".conf",
}
_MAX_TEXT_BYTES = 10_000  # max bytes to include per text file
_MAX_TEXT_FILES = 10      # max number of text files to include
_MAX_DECOMP_BYTES = 50_000_000  # 50 MB hard ceiling — zip bomb protection


def extract_listing(file_path: str, raw_bytes: bytes) -> str | None:
    ext = Path(file_path).suffix.lower()
    try:
        if ext in (".zip",):
            return _zip(raw_bytes)
        elif ext in (".gz",) and not file_path.endswith(".tar.gz"):
            return _gz_single(file_path, raw_bytes)
        else:
            return _tar(raw_bytes)
    except Exception:
        return None


def _zip(data: bytes) -> str | None:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        lines = [f"ZIP archive — {len(names)} entries:", ""]
        text_included = 0
        for name in names:
            info = zf.getinfo(name)
            size = info.file_size
            lines.append(f"  {name}  ({size:,} bytes)")
            ext = Path(name).suffix.lower()
            if ext in _TEXT_EXTS and size <= _MAX_TEXT_BYTES and text_included < _MAX_TEXT_FILES:
                try:
                    raw = zf.read(name)
                    if len(raw) > _MAX_DECOMP_BYTES:
                        continue  # skip single oversized entry
                    content = raw.decode("utf-8", errors="replace")
                    lines.append(f"    Content:\n{content}\n")
                    text_included += 1
                except Exception:
                    pass
    return "\n".join(lines)


def _tar(data: bytes) -> str | None:
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        members = tf.getmembers()
        lines = [f"TAR archive — {len(members)} entries:", ""]
        text_included = 0
        for m in members:
            lines.append(f"  {m.name}  ({m.size:,} bytes)")
            ext = Path(m.name).suffix.lower()
            if (m.isfile() and ext in _TEXT_EXTS and
                    m.size <= _MAX_TEXT_BYTES and text_included < _MAX_TEXT_FILES):
                try:
                    f = tf.extractfile(m)
                    if f:
                        content = f.read().decode("utf-8", errors="replace")
                        lines.append(f"    Content:\n{content}\n")
                        text_included += 1
                except Exception:
                    pass
    return "\n".join(lines)


def _gz_single(file_path: str, data: bytes) -> str | None:
    inner_name = Path(file_path).stem
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
        raw = gz.read(_MAX_DECOMP_BYTES)
    content = raw.decode("utf-8", errors="replace")
    return f"GZ-compressed file: {inner_name}\n\n{content[:_MAX_TEXT_BYTES]}"
