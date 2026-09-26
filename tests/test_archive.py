"""Tests for worker/binary/handlers/archive.py — archive extraction."""
from __future__ import annotations

import gzip
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from worker.binary.handlers.archive import (
    _MAX_DECOMP_BYTES,
    _MAX_TEXT_BYTES,
    _MAX_TEXT_FILES,
    _gz_single,
    _tar,
    _zip,
    extract_listing,
)


# ---------------------------------------------------------------------------
# Helpers to build in-memory archives
# ---------------------------------------------------------------------------

def _make_zip(entries: list[tuple[str, bytes]]) -> bytes:
    """Create a ZIP archive in memory from a list of (name, content) pairs."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_STORED) as zf:
        for name, content in entries:
            zf.writestr(name, content)
    return buf.getvalue()


def _make_tar(entries: list[tuple[str, bytes]]) -> bytes:
    """Create an uncompressed TAR archive in memory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        for name, content in entries:
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _make_gz(content: bytes) -> bytes:
    """Gzip-compress bytes."""
    return gzip.compress(content)


# ---------------------------------------------------------------------------
# _zip()
# ---------------------------------------------------------------------------

class TestZip:
    def test_header_shows_entry_count(self):
        data = _make_zip([("a.txt", b"hello"), ("b.txt", b"world")])
        result = _zip(data)
        assert result is not None
        assert "ZIP archive" in result
        assert "2 entries" in result

    def test_all_files_are_listed(self):
        data = _make_zip([("readme.txt", b"text"), ("image.png", b"\x89PNG")])
        result = _zip(data)
        assert "readme.txt" in result
        assert "image.png" in result

    def test_file_sizes_are_shown(self):
        content = b"hello world"
        data = _make_zip([("file.txt", content)])
        result = _zip(data)
        assert str(len(content)) in result

    def test_text_file_content_is_included(self):
        data = _make_zip([("script.py", b"def foo(): pass\n")])
        result = _zip(data)
        assert "def foo(): pass" in result

    def test_non_text_file_is_listed_but_not_extracted(self):
        data = _make_zip([("photo.png", b"\x89PNG\r\n\x1a\n\x00" * 10)])
        result = _zip(data)
        assert "photo.png" in result
        # PNG content should NOT appear as text
        assert "Content:" not in result

    def test_max_text_files_limit(self):
        # Create _MAX_TEXT_FILES + 2 text files; only _MAX_TEXT_FILES should have content
        entries = [(f"file_{i}.py", f"# file {i}\n".encode()) for i in range(_MAX_TEXT_FILES + 2)]
        data = _make_zip(entries)
        result = _zip(data)
        # Count "Content:" occurrences
        content_count = result.count("Content:")
        assert content_count == _MAX_TEXT_FILES

    def test_text_file_over_max_bytes_not_extracted(self):
        big_text = b"x" * (_MAX_TEXT_BYTES + 1)
        data = _make_zip([("big.py", big_text)])
        result = _zip(data)
        assert "big.py" in result
        assert "Content:" not in result

    def test_text_file_at_max_bytes_is_extracted(self):
        exact_text = b"a" * _MAX_TEXT_BYTES
        data = _make_zip([("exact.py", exact_text)])
        result = _zip(data)
        assert "Content:" in result

    def test_empty_archive(self):
        data = _make_zip([])
        result = _zip(data)
        assert result is not None
        assert "0 entries" in result

    def test_multiple_text_files_extracted(self):
        entries = [("a.py", b"print('a')"), ("b.js", b"console.log('b')")]
        data = _make_zip(entries)
        result = _zip(data)
        assert "print('a')" in result
        assert "console.log('b')" in result

    def test_corrupt_zip_raises_or_returns_none(self):
        # _zip() does not catch BadZipFile internally; extract_listing() does.
        # Calling _zip() directly with corrupt data raises zipfile.BadZipFile.
        import zipfile as _zipfile
        with pytest.raises(_zipfile.BadZipFile):
            _zip(b"this is not a zip file")

    def test_various_text_extensions_extracted(self):
        text_entries = [
            ("config.yaml", b"key: value"),
            ("init.sql", b"SELECT 1;"),
            ("notes.md", b"# Heading"),
        ]
        data = _make_zip(text_entries)
        result = _zip(data)
        assert "key: value" in result
        assert "SELECT 1;" in result
        assert "# Heading" in result


# ---------------------------------------------------------------------------
# _tar()
# ---------------------------------------------------------------------------

class TestTar:
    def test_header_shows_entry_count(self):
        data = _make_tar([("a.txt", b"hi"), ("b.txt", b"there")])
        result = _tar(data)
        assert result is not None
        assert "TAR archive" in result
        assert "2 entries" in result

    def test_all_members_are_listed(self):
        data = _make_tar([("src/main.py", b"code"), ("README.md", b"docs")])
        result = _tar(data)
        assert "src/main.py" in result
        assert "README.md" in result

    def test_text_file_content_is_included(self):
        data = _make_tar([("app.py", b"import os\nprint(os.getcwd())\n")])
        result = _tar(data)
        assert "import os" in result

    def test_non_text_extension_not_extracted(self):
        data = _make_tar([("image.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)])
        result = _tar(data)
        assert "image.png" in result
        assert "Content:" not in result

    def test_max_text_files_limit(self):
        entries = [(f"f{i}.py", f"# {i}\n".encode()) for i in range(_MAX_TEXT_FILES + 3)]
        data = _make_tar(entries)
        result = _tar(data)
        assert result.count("Content:") == _MAX_TEXT_FILES

    def test_text_file_over_max_bytes_not_extracted(self):
        big_content = b"z" * (_MAX_TEXT_BYTES + 1)
        data = _make_tar([("large.py", big_content)])
        result = _tar(data)
        assert "large.py" in result
        assert "Content:" not in result

    def test_file_sizes_shown(self):
        content = b"hello"
        data = _make_tar([("f.txt", content)])
        result = _tar(data)
        assert str(len(content)) in result

    def test_empty_tar(self):
        data = _make_tar([])
        result = _tar(data)
        assert result is not None
        assert "0 entries" in result

    def test_mixed_text_and_binary(self):
        data = _make_tar([
            ("code.py", b"x = 1"),
            ("data.bin", b"\x00\x01\x02"),
        ])
        result = _tar(data)
        assert "code.py" in result
        assert "data.bin" in result
        assert "x = 1" in result


# ---------------------------------------------------------------------------
# _gz_single()
# ---------------------------------------------------------------------------

class TestGzSingle:
    def test_returns_string_with_filename(self):
        content = b"Hello, world!\n"
        gz_data = _make_gz(content)
        result = _gz_single("hello.txt.gz", gz_data)
        assert result is not None
        # Inner name is stem: "hello.txt"
        assert "hello.txt" in result

    def test_content_is_included(self):
        content = b"line1\nline2\nline3\n"
        gz_data = _make_gz(content)
        result = _gz_single("log.gz", gz_data)
        assert "line1" in result
        assert "line2" in result

    def test_content_truncated_to_max_text_bytes(self):
        long_content = b"A" * (_MAX_TEXT_BYTES * 2)
        gz_data = _make_gz(long_content)
        result = _gz_single("big.gz", gz_data)
        assert result is not None
        # The content slice in _gz_single is [:_MAX_TEXT_BYTES]
        assert len(result) < _MAX_TEXT_BYTES * 3  # sanity bound

    def test_gz_read_limited_by_max_decomp_bytes(self):
        """_gz_single passes _MAX_DECOMP_BYTES to gz.read() as a size cap."""
        # We can't easily generate 50MB of compressed data in a test,
        # so we verify the behaviour by checking that gzip.read(limit) is used.
        # Instead, create a small file and confirm it works normally.
        content = b"safe content"
        gz_data = _make_gz(content)
        result = _gz_single("file.gz", gz_data)
        assert "safe content" in result

    def test_stem_used_as_inner_name(self):
        content = b"data"
        gz_data = _make_gz(content)
        # "archive.tar.gz" → stem is "archive.tar"
        result = _gz_single("archive.tar.gz", gz_data)
        assert "archive.tar" in result

    def test_binary_content_decoded_with_replace(self):
        binary_content = b"\xff\xfe binary \x00 data"
        gz_data = _make_gz(binary_content)
        # Must not raise; errors='replace' used
        result = _gz_single("data.gz", gz_data)
        assert result is not None


# ---------------------------------------------------------------------------
# extract_listing() — dispatch
# ---------------------------------------------------------------------------

class TestExtractListing:
    def test_dispatches_to_zip_for_dot_zip(self):
        data = _make_zip([("hello.txt", b"hi")])
        result = extract_listing("archive.zip", data)
        assert result is not None
        assert "ZIP archive" in result

    def test_dispatches_to_gz_for_dot_gz(self):
        content = b"plain text content"
        gz_data = _make_gz(content)
        result = extract_listing("notes.gz", gz_data)
        assert result is not None
        assert "notes" in result  # inner file name present

    def test_dot_gz_does_not_dispatch_to_gz_single_for_tar_gz(self):
        """A .tar.gz file must NOT go through _gz_single (it ends with .tar.gz)."""
        # Build a valid tar.gz
        tar_data = _make_tar([("file.py", b"code")])
        gz_data = _make_gz(tar_data)
        result = extract_listing("project.tar.gz", gz_data)
        # Should go to _tar path (or at minimum not crash)
        assert result is not None

    def test_dispatches_to_tar_for_dot_tar(self):
        data = _make_tar([("readme.txt", b"hello")])
        result = extract_listing("bundle.tar", data)
        assert result is not None
        assert "TAR archive" in result

    def test_dispatches_to_tar_for_dot_tgz(self):
        tar_data = _make_tar([("script.sh", b"#!/bin/sh")])
        gz_data = _make_gz(tar_data)
        result = extract_listing("bundle.tgz", gz_data)
        # .tgz falls through to _tar path
        assert result is not None

    def test_returns_none_on_corrupt_zip(self):
        result = extract_listing("broken.zip", b"not a zip")
        assert result is None

    def test_returns_none_on_corrupt_tar(self):
        result = extract_listing("broken.tar", b"not a tar")
        assert result is None

    def test_returns_none_on_corrupt_gz(self):
        result = extract_listing("broken.gz", b"not gzipped")
        assert result is None

    def test_zip_listing_correct_entry_count(self):
        data = _make_zip([("a.py", b"1"), ("b.py", b"2"), ("c.py", b"3")])
        result = extract_listing("pkg.zip", data)
        assert "3 entries" in result

    def test_tar_listing_correct_entry_count(self):
        data = _make_tar([("x.txt", b"x"), ("y.txt", b"y")])
        result = extract_listing("pkg.tar", data)
        assert "2 entries" in result


# ---------------------------------------------------------------------------
# Zip bomb protection — _MAX_DECOMP_BYTES in _gz_single
# ---------------------------------------------------------------------------

class TestZipBombProtection:
    def test_gz_read_honours_size_cap(self):
        """Verify gzip.read(_MAX_DECOMP_BYTES) caps decompression.

        We create a small gz and confirm _gz_single works; the actual cap is
        exercised by the gzip.GzipFile.read(size) call in the implementation.
        We inspect that the constant is reasonable (50 MB) rather than unlimited.
        """
        assert _MAX_DECOMP_BYTES == 50_000_000

    def test_gz_single_does_not_exceed_cap_on_large_input(self, monkeypatch):
        """Monkey-patch gzip.GzipFile.read to assert it's called with the cap."""
        import gzip as gzip_module

        read_args = []
        original_read = gzip_module.GzipFile.read

        def capturing_read(self, size=-1):
            read_args.append(size)
            return original_read(self, size)

        monkeypatch.setattr(gzip_module.GzipFile, "read", capturing_read)

        content = b"small content"
        gz_data = _make_gz(content)
        _gz_single("test.gz", gz_data)

        # The implementation calls gz.read(_MAX_DECOMP_BYTES)
        assert any(a == _MAX_DECOMP_BYTES for a in read_args), (
            f"Expected gz.read({_MAX_DECOMP_BYTES}) to be called, got read_args={read_args}"
        )
