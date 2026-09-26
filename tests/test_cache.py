"""Tests for worker/cache.py — persistent summary cache."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import worker.cache as cache_module
from worker.cache import CacheEntry, _evict, enabled, get, get_stale, put


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(**kwargs) -> dict:
    """Return a raw cache dict entry (as stored on disk)."""
    defaults = dict(
        fingerprint="abc123",
        summary="This module does X.",
        delta_count=0,
        content="def foo(): pass",
        created_at=time.time(),
    )
    defaults.update(kwargs)
    return defaults


def _write_cache(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# enabled()
# ---------------------------------------------------------------------------

class TestEnabled:
    def test_true_when_cache_file_set(self, monkeypatch):
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", "/tmp/cache.json")
        assert cache_module.enabled() is True

    def test_false_when_cache_file_empty(self, monkeypatch):
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", "")
        assert cache_module.enabled() is False

    def test_false_when_cache_file_not_set(self, monkeypatch):
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", "")
        assert not cache_module.enabled()


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------

class TestGet:
    def test_returns_none_when_disabled(self, monkeypatch):
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", "")
        assert get("/some/file.py") is None

    def test_returns_none_when_no_entry(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "cache.json"
        _write_cache(cache_file, {})
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", str(cache_file))
        with patch.object(cache_module, "_fingerprint", return_value="fp123"):
            assert get("/no/such/file.py") is None

    def test_returns_none_when_ttl_expired(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "cache.json"
        expired_time = time.time() - (8 * 24 * 3600)  # 8 days ago
        entry = _make_entry(fingerprint="fp123", created_at=expired_time)
        _write_cache(cache_file, {"/some/file.py": entry})
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", str(cache_file))
        with patch.object(cache_module, "_fingerprint", return_value="fp123"):
            assert get("/some/file.py") is None

    def test_returns_entry_when_valid(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "cache.json"
        entry = _make_entry(fingerprint="fp123", summary="Valid summary")
        _write_cache(cache_file, {"/some/file.py": entry})
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", str(cache_file))
        with patch.object(cache_module, "_fingerprint", return_value="fp123"):
            result = get("/some/file.py")
        assert result is not None
        assert isinstance(result, CacheEntry)
        assert result.summary == "Valid summary"
        assert result.fingerprint == "fp123"

    def test_returns_none_on_fingerprint_mismatch(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "cache.json"
        entry = _make_entry(fingerprint="old_fp")
        _write_cache(cache_file, {"/some/file.py": entry})
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", str(cache_file))
        with patch.object(cache_module, "_fingerprint", return_value="new_fp"):
            assert get("/some/file.py") is None

    def test_returns_none_when_fingerprint_is_none(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "cache.json"
        entry = _make_entry(fingerprint="fp123")
        _write_cache(cache_file, {"/some/file.py": entry})
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", str(cache_file))
        with patch.object(cache_module, "_fingerprint", return_value=None):
            assert get("/some/file.py") is None

    def test_returns_none_for_malformed_entry(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "cache.json"
        # Entry missing required fields
        _write_cache(cache_file, {"/some/file.py": {"garbage": True}})
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", str(cache_file))
        with patch.object(cache_module, "_fingerprint", return_value="fp123"):
            assert get("/some/file.py") is None

    def test_returns_none_exactly_at_ttl_boundary(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "cache.json"
        ttl = 7 * 24 * 3600
        # Just over the TTL limit
        old_time = time.time() - ttl - 1
        entry = _make_entry(fingerprint="fp123", created_at=old_time)
        _write_cache(cache_file, {"/some/file.py": entry})
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", str(cache_file))
        with patch.object(cache_module, "_fingerprint", return_value="fp123"):
            assert get("/some/file.py") is None

    def test_preserves_all_fields(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "cache.json"
        created = time.time() - 60
        entry = _make_entry(
            fingerprint="fp_abc",
            summary="My summary",
            delta_count=3,
            content="source content",
            created_at=created,
        )
        _write_cache(cache_file, {"/path/file.py": entry})
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", str(cache_file))
        with patch.object(cache_module, "_fingerprint", return_value="fp_abc"):
            result = get("/path/file.py")
        assert result.delta_count == 3
        assert result.content == "source content"
        assert result.created_at == pytest.approx(created, abs=0.001)


# ---------------------------------------------------------------------------
# get_stale()
# ---------------------------------------------------------------------------

class TestGetStale:
    def test_returns_none_when_disabled(self, monkeypatch):
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", "")
        assert get_stale("/file.py") is None

    def test_returns_entry_regardless_of_fingerprint_mismatch(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "cache.json"
        entry = _make_entry(fingerprint="old_fp", summary="Stale summary")
        _write_cache(cache_file, {"/file.py": entry})
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", str(cache_file))
        # Fingerprint doesn't matter for get_stale
        result = get_stale("/file.py")
        assert result is not None
        assert result.summary == "Stale summary"

    def test_returns_none_when_entry_expired(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "cache.json"
        expired_time = time.time() - (8 * 24 * 3600)
        entry = _make_entry(fingerprint="fp", created_at=expired_time)
        _write_cache(cache_file, {"/file.py": entry})
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", str(cache_file))
        assert get_stale("/file.py") is None

    def test_returns_none_when_no_entry(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "cache.json"
        _write_cache(cache_file, {})
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", str(cache_file))
        assert get_stale("/not/there.py") is None

    def test_returns_entry_with_different_fingerprint(self, tmp_path, monkeypatch):
        """get_stale should NOT reject entries due to fingerprint change."""
        cache_file = tmp_path / "cache.json"
        entry = _make_entry(fingerprint="sha_old")
        _write_cache(cache_file, {"/file.py": entry})
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", str(cache_file))
        with patch.object(cache_module, "_fingerprint", return_value="sha_new"):
            result = get_stale("/file.py")
        assert result is not None
        assert result.fingerprint == "sha_old"


# ---------------------------------------------------------------------------
# put()
# ---------------------------------------------------------------------------

class TestPut:
    def test_creates_entry_readable_by_get(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "cache.json"
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", str(cache_file))
        with patch.object(cache_module, "_fingerprint", return_value="fp_new"):
            put("/my/file.py", "Great summary", 0, "content here")
            result = get("/my/file.py")
        assert result is not None
        assert result.summary == "Great summary"
        assert result.content == "content here"
        assert result.delta_count == 0
        assert result.fingerprint == "fp_new"

    def test_does_not_raise_when_disabled(self, monkeypatch):
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", "")
        # Must not raise
        put("/file.py", "summary", 0, "content")

    def test_does_not_raise_on_disk_error(self, monkeypatch):
        # Point cache file to an unwritable location
        monkeypatch.setattr(
            "worker.cache.config.TRIM_CACHE_FILE",
            "/proc/1/mem",  # unwritable on macOS/Linux
        )
        with patch.object(cache_module, "_fingerprint", return_value="fp"):
            # Must never raise
            put("/file.py", "summary", 0, "content")

    def test_put_skips_when_fingerprint_none(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "cache.json"
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", str(cache_file))
        with patch.object(cache_module, "_fingerprint", return_value=None):
            put("/file.py", "summary", 0, "content")
        # Cache file should not be created or should be empty/missing entry
        if cache_file.exists():
            data = json.loads(cache_file.read_text())
            assert "/file.py" not in data

    def test_overwrites_existing_entry(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "cache.json"
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", str(cache_file))
        with patch.object(cache_module, "_fingerprint", return_value="fp"):
            put("/file.py", "First summary", 0, "v1")
            put("/file.py", "Second summary", 1, "v2")
            result = get("/file.py")
        assert result.summary == "Second summary"
        assert result.delta_count == 1

    def test_creates_parent_directories(self, tmp_path, monkeypatch):
        nested = tmp_path / "a" / "b" / "c" / "cache.json"
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", str(nested))
        with patch.object(cache_module, "_fingerprint", return_value="fp"):
            put("/file.py", "summary", 0, "content")
        assert nested.exists()

    def test_multiple_entries_coexist(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "cache.json"
        monkeypatch.setattr("worker.cache.config.TRIM_CACHE_FILE", str(cache_file))
        with patch.object(cache_module, "_fingerprint", return_value="fp"):
            put("/file_a.py", "Summary A", 0, "content A")
            put("/file_b.py", "Summary B", 0, "content B")
        with patch.object(cache_module, "_fingerprint", return_value="fp"):
            result_a = get("/file_a.py")
            result_b = get("/file_b.py")
        assert result_a.summary == "Summary A"
        assert result_b.summary == "Summary B"


# ---------------------------------------------------------------------------
# _evict()
# ---------------------------------------------------------------------------

class TestEvict:
    def test_removes_expired_entries(self):
        now = time.time()
        ttl = 7 * 24 * 3600
        cache = {
            "/fresh.py": _make_entry(created_at=now - 3600),           # 1 hour old — keep
            "/expired.py": _make_entry(created_at=now - ttl - 100),    # just over TTL — remove
            "/very_old.py": _make_entry(created_at=now - 30 * 24 * 3600),  # 30 days — remove
        }
        _evict(cache)
        assert "/fresh.py" in cache
        assert "/expired.py" not in cache
        assert "/very_old.py" not in cache

    def test_does_not_remove_fresh_entries(self):
        now = time.time()
        cache = {
            "/a.py": _make_entry(created_at=now - 10),
            "/b.py": _make_entry(created_at=now - 3600),
        }
        _evict(cache)
        assert len(cache) == 2

    def test_evicts_oldest_when_over_max_entries(self):
        now = time.time()
        cache = {}
        # Add 502 fresh entries
        for i in range(502):
            path = f"/file_{i:04d}.py"
            # Spread timestamps so oldest is deterministic
            cache[path] = _make_entry(created_at=now - (502 - i))
        _evict(cache)
        assert len(cache) <= 500

    def test_evicts_oldest_entries_lru(self):
        now = time.time()
        cache = {}
        # Create exactly 502 entries where the oldest two are identifiable
        for i in range(502):
            path = f"/file_{i:04d}.py"
            cache[path] = _make_entry(created_at=now - (502 - i))
        # The oldest are file_0000.py and file_0001.py
        _evict(cache)
        assert "/file_0000.py" not in cache
        assert "/file_0001.py" not in cache

    def test_evicts_expired_before_lru_check(self):
        now = time.time()
        ttl = 7 * 24 * 3600
        cache = {}
        # 498 fresh entries + 2 expired = 500 total; no LRU eviction needed after expiry removal
        for i in range(498):
            cache[f"/fresh_{i}.py"] = _make_entry(created_at=now - 60)
        cache["/exp_a.py"] = _make_entry(created_at=now - ttl - 1)
        cache["/exp_b.py"] = _make_entry(created_at=now - ttl - 1)
        _evict(cache)
        assert "/exp_a.py" not in cache
        assert "/exp_b.py" not in cache
        # After removing 2 expired entries only 498 remain, no LRU needed
        assert len(cache) == 498

    def test_empty_cache_is_noop(self):
        cache = {}
        _evict(cache)  # must not raise
        assert cache == {}

    def test_exactly_500_entries_no_eviction(self):
        now = time.time()
        cache = {f"/f_{i}.py": _make_entry(created_at=now - i) for i in range(500)}
        _evict(cache)
        assert len(cache) == 500

    def test_exactly_501_entries_evicts_one(self):
        now = time.time()
        cache = {f"/f_{i}.py": _make_entry(created_at=now - (501 - i)) for i in range(501)}
        _evict(cache)
        assert len(cache) == 500
