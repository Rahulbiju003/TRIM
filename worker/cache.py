"""Persistent summary cache for diff-aware summarization.

Cache file:   TRIM_CACHE_FILE (unset = caching disabled)
Cache key:    absolute file path
Fingerprint:  git blob hash when available, else "{mtime:.0f}-{size}" string
TTL:          7 days
Max entries:  500 (LRU eviction when full)
Concurrency:  fcntl shared/exclusive locks — safe for parallel hook processes
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from worker import config

_TTL_SECONDS = 7 * 24 * 3600
_MAX_ENTRIES = 500


@dataclass
class CacheEntry:
    fingerprint: str    # git blob hash or "mtime-size" string
    summary: str        # last generated summary
    delta_count: int    # delta updates applied since last full summarization
    content: str        # file content at time of caching (for difflib fallback)
    created_at: float   # unix timestamp


def enabled() -> bool:
    """Return True when caching is configured."""
    return bool(config.TRIM_CACHE_FILE)


def get(file_path: str) -> CacheEntry | None:
    """Return a valid, unexpired cache entry or None.

    Returns None when:
    - Caching is disabled
    - No entry exists for this path
    - Entry is expired (> 7 days old)
    - File fingerprint has changed (file was modified)
    """
    if not enabled():
        return None
    cache = _load()
    raw = cache.get(file_path)
    if not raw:
        return None
    try:
        entry = CacheEntry(**raw)
    except (TypeError, KeyError):
        return None
    if time.time() - entry.created_at > _TTL_SECONDS:
        return None
    current_fp = _fingerprint(file_path)
    if current_fp is None or current_fp != entry.fingerprint:
        return None
    return entry


def get_stale(file_path: str) -> CacheEntry | None:
    """Return a cache entry even if the fingerprint changed (file was modified).

    Used by the delta path: we need the previous summary and content even
    though the file is now different. Returns None if no entry exists or expired.
    """
    if not enabled():
        return None
    cache = _load()
    raw = cache.get(file_path)
    if not raw:
        return None
    try:
        entry = CacheEntry(**raw)
    except (TypeError, KeyError):
        return None
    if time.time() - entry.created_at > _TTL_SECONDS:
        return None
    return entry


def put(file_path: str, summary: str, delta_count: int, content: str) -> None:
    """Write or update a cache entry. Never raises."""
    if not enabled():
        return
    try:
        fp = _fingerprint(file_path)
        if fp is None:
            return
        cache = _load()
        cache[file_path] = asdict(CacheEntry(
            fingerprint=fp,
            summary=summary,
            delta_count=delta_count,
            content=content,
            created_at=time.time(),
        ))
        _evict(cache)
        _save(cache)
    except Exception:
        pass  # cache writes must never crash the main path


def _fingerprint(file_path: str) -> str | None:
    """Git blob hash if tracked, else mtime-size string. None on error."""
    try:
        result = subprocess.run(
            ["git", "hash-object", file_path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    try:
        stat = os.stat(file_path)
        return f"{stat.st_mtime:.0f}-{stat.st_size}"
    except Exception:
        return None


def _load() -> dict:
    path = Path(config.TRIM_CACHE_FILE)
    if not path.exists():
        return {}
    try:
        fd = os.open(str(path), os.O_RDONLY)
        with os.fdopen(fd) as fh:
            fcntl.flock(fh, fcntl.LOCK_SH)
            return json.load(fh)
    except Exception:
        return {}


def _save(cache: dict) -> None:
    path = Path(config.TRIM_CACHE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        json.dump(cache, fh)


def _evict(cache: dict) -> None:
    """Remove expired entries; LRU-evict if still over the limit."""
    now = time.time()
    expired = [k for k, v in cache.items() if now - v.get("created_at", 0) > _TTL_SECONDS]
    for k in expired:
        del cache[k]
    while len(cache) > _MAX_ENTRIES:
        oldest = min(cache, key=lambda k: cache[k].get("created_at", 0))
        del cache[oldest]
