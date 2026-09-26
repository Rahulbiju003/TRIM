"""Tests for worker/binary/detector.py — binary type detection."""
from __future__ import annotations

import pytest

from worker.binary.detector import BinaryType, detect_type, is_binary


# ---------------------------------------------------------------------------
# is_binary()
# ---------------------------------------------------------------------------

class TestIsBinary:
    def test_true_when_null_byte_present(self):
        assert is_binary(b"some\x00data") is True

    def test_true_when_only_null_bytes(self):
        assert is_binary(b"\x00\x00\x00") is True

    def test_true_when_null_byte_at_start(self):
        assert is_binary(b"\x00hello") is True

    def test_true_when_null_byte_at_end(self):
        assert is_binary(b"hello\x00") is True

    def test_false_for_plain_text(self):
        assert is_binary(b"hello world\n") is False

    def test_false_for_empty_bytes(self):
        assert is_binary(b"") is False

    def test_false_for_printable_bytes(self):
        assert is_binary(b"def foo(): pass\n") is False


# ---------------------------------------------------------------------------
# detect_type() — magic byte detection
# ---------------------------------------------------------------------------

class TestMagicBytes:
    """Each magic signature leads to the correct BinaryType."""

    def test_pdf_magic(self):
        header = b"%PDF-1.4 some content"
        assert detect_type("file.bin", header) == BinaryType.PDF

    def test_png_magic(self):
        header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        assert detect_type("file.bin", header) == BinaryType.IMAGE

    def test_jpeg_magic(self):
        header = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        assert detect_type("file.bin", header) == BinaryType.IMAGE

    def test_gif_magic(self):
        header = b"GIF87a" + b"\x00" * 100
        assert detect_type("file.bin", header) == BinaryType.IMAGE

    def test_gif89_magic(self):
        header = b"GIF89a" + b"\x00" * 100
        assert detect_type("file.bin", header) == BinaryType.IMAGE

    def test_bmp_magic(self):
        header = b"BM" + b"\x00" * 100
        assert detect_type("file.bin", header) == BinaryType.IMAGE

    def test_zip_magic(self):
        header = b"PK\x03\x04" + b"\x00" * 100
        assert detect_type("file.zip", header) == BinaryType.ARCHIVE

    def test_gzip_magic(self):
        header = b"\x1f\x8b\x08" + b"\x00" * 100
        assert detect_type("file.gz", header) == BinaryType.ARCHIVE

    def test_bzip2_magic(self):
        header = b"BZh9" + b"\x00" * 100
        assert detect_type("file.bz2", header) == BinaryType.ARCHIVE

    def test_xz_magic(self):
        header = b"\xfd7zXZ\x00" + b"\x00" * 100
        assert detect_type("file.xz", header) == BinaryType.ARCHIVE

    def test_rar_magic(self):
        header = b"Rar!\x1a\x07" + b"\x00" * 100
        assert detect_type("file.rar", header) == BinaryType.ARCHIVE

    def test_7z_magic(self):
        header = b"7z\xbc\xaf\x27\x1c" + b"\x00" * 100
        assert detect_type("file.7z", header) == BinaryType.ARCHIVE

    def test_sqlite_magic(self):
        header = b"SQLite format 3\x00" + b"\x00" * 100
        assert detect_type("file.db", header) == BinaryType.DATABASE


# ---------------------------------------------------------------------------
# RIFF / WEBP corner cases
# ---------------------------------------------------------------------------

class TestRIFF:
    """RIFF magic is ambiguous — only IMAGE when bytes 8-11 say WEBP."""

    def _riff_header(self, sub_type: bytes) -> bytes:
        # RIFF header: 'RIFF' (4) + file size (4) + sub-type (4) + padding
        return b"RIFF" + b"\x00\x00\x00\x00" + sub_type + b"\x00" * 100

    def test_riff_webp_is_image(self):
        header = self._riff_header(b"WEBP")
        assert detect_type("file.webp", header) == BinaryType.IMAGE

    def test_riff_wav_falls_through_to_unknown(self):
        # WAV is not in any extension list when file_path has no recognised ext
        header = self._riff_header(b"WAVE")
        result = detect_type("audio.wav", header)
        # .wav is not in _IMAGE_EXTS, _ARCHIVE_EXTS, _OFFICE_EXTS, _DB_EXTS
        assert result == BinaryType.UNKNOWN

    def test_riff_avi_falls_through_to_unknown(self):
        header = self._riff_header(b"AVI ")
        result = detect_type("video.avi", header)
        assert result == BinaryType.UNKNOWN

    def test_riff_non_webp_generic_extension_unknown(self):
        header = self._riff_header(b"XXXX")
        assert detect_type("mystery.bin", header) == BinaryType.UNKNOWN


# ---------------------------------------------------------------------------
# ZIP + Office extension → OFFICE
# ---------------------------------------------------------------------------

class TestZIPOfficeXML:
    """ZIP magic bytes + Office extension → OFFICE (not ARCHIVE)."""

    _ZIP_HEADER = b"PK\x03\x04" + b"\x00" * 100

    def test_zip_plus_docx_is_office(self):
        assert detect_type("report.docx", self._ZIP_HEADER) == BinaryType.OFFICE

    def test_zip_plus_xlsx_is_office(self):
        assert detect_type("sheet.xlsx", self._ZIP_HEADER) == BinaryType.OFFICE

    def test_zip_plus_pptx_is_office(self):
        assert detect_type("slide.pptx", self._ZIP_HEADER) == BinaryType.OFFICE

    def test_zip_plus_odt_is_office(self):
        assert detect_type("doc.odt", self._ZIP_HEADER) == BinaryType.OFFICE

    def test_zip_plus_ods_is_office(self):
        assert detect_type("sheet.ods", self._ZIP_HEADER) == BinaryType.OFFICE

    def test_zip_plus_odp_is_office(self):
        assert detect_type("pres.odp", self._ZIP_HEADER) == BinaryType.OFFICE

    def test_zip_plus_zip_extension_is_archive(self):
        assert detect_type("archive.zip", self._ZIP_HEADER) == BinaryType.ARCHIVE

    def test_zip_plus_no_extension_is_archive(self):
        assert detect_type("noext", self._ZIP_HEADER) == BinaryType.ARCHIVE


# ---------------------------------------------------------------------------
# Extension fallback (no matching magic bytes)
# ---------------------------------------------------------------------------

class TestExtensionFallback:
    """When magic bytes don't match, extension drives the result."""

    # Use bytes that won't match any magic signature
    _INERT = b"\x42\x43\x44\x45" + b"\x41" * 100

    def test_pdf_extension(self):
        assert detect_type("document.pdf", self._INERT) == BinaryType.PDF

    def test_png_extension(self):
        assert detect_type("photo.png", self._INERT) == BinaryType.IMAGE

    def test_jpg_extension(self):
        assert detect_type("photo.jpg", self._INERT) == BinaryType.IMAGE

    def test_jpeg_extension(self):
        assert detect_type("photo.jpeg", self._INERT) == BinaryType.IMAGE

    def test_gif_extension(self):
        assert detect_type("anim.gif", self._INERT) == BinaryType.IMAGE

    def test_webp_extension(self):
        assert detect_type("img.webp", self._INERT) == BinaryType.IMAGE

    def test_bmp_extension(self):
        assert detect_type("icon.bmp", self._INERT) == BinaryType.IMAGE

    def test_tiff_extension(self):
        assert detect_type("scan.tiff", self._INERT) == BinaryType.IMAGE

    def test_tif_extension(self):
        assert detect_type("scan.tif", self._INERT) == BinaryType.IMAGE

    def test_svg_extension(self):
        assert detect_type("vector.svg", self._INERT) == BinaryType.IMAGE

    def test_tar_extension(self):
        assert detect_type("archive.tar", self._INERT) == BinaryType.ARCHIVE

    def test_gz_extension(self):
        assert detect_type("file.gz", self._INERT) == BinaryType.ARCHIVE

    def test_bz2_extension(self):
        assert detect_type("file.bz2", self._INERT) == BinaryType.ARCHIVE

    def test_xz_extension(self):
        assert detect_type("file.xz", self._INERT) == BinaryType.ARCHIVE

    def test_rar_extension(self):
        assert detect_type("file.rar", self._INERT) == BinaryType.ARCHIVE

    def test_7z_extension(self):
        assert detect_type("file.7z", self._INERT) == BinaryType.ARCHIVE

    def test_tgz_extension(self):
        assert detect_type("bundle.tgz", self._INERT) == BinaryType.ARCHIVE

    def test_docx_extension(self):
        assert detect_type("report.docx", self._INERT) == BinaryType.OFFICE

    def test_xlsx_extension(self):
        assert detect_type("data.xlsx", self._INERT) == BinaryType.OFFICE

    def test_pptx_extension(self):
        assert detect_type("pres.pptx", self._INERT) == BinaryType.OFFICE

    def test_doc_extension(self):
        assert detect_type("old.doc", self._INERT) == BinaryType.OFFICE

    def test_xls_extension(self):
        assert detect_type("old.xls", self._INERT) == BinaryType.OFFICE

    def test_ppt_extension(self):
        assert detect_type("old.ppt", self._INERT) == BinaryType.OFFICE

    def test_db_extension(self):
        assert detect_type("data.db", self._INERT) == BinaryType.DATABASE

    def test_sqlite_extension(self):
        assert detect_type("data.sqlite", self._INERT) == BinaryType.DATABASE

    def test_sqlite3_extension(self):
        assert detect_type("data.sqlite3", self._INERT) == BinaryType.DATABASE

    def test_unknown_extension(self):
        assert detect_type("file.xyz", self._INERT) == BinaryType.UNKNOWN

    def test_no_extension(self):
        assert detect_type("Makefile", self._INERT) == BinaryType.UNKNOWN

    def test_empty_header_and_unknown_extension(self):
        assert detect_type("binary.bin", b"") == BinaryType.UNKNOWN

    def test_extension_case_insensitive_pdf(self):
        assert detect_type("DOC.PDF", self._INERT) == BinaryType.PDF

    def test_extension_case_insensitive_png(self):
        assert detect_type("image.PNG", self._INERT) == BinaryType.IMAGE
