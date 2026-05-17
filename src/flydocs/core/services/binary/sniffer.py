# Copyright 2026 Firefly Software Solutions Inc
"""Magic-byte sniffer covering every format the binary normalizer handles.

Kept as a free function rather than a service: it has no dependencies,
no state, and is called from both the normalizer and the legacy loader.
The same return values double as the routing key -- the normalizer
dispatches per sniffed type to the appropriate adapter.
"""

from __future__ import annotations

# Format magic bytes. Sources: file(1) magic database, RFC 1952 (gzip),
# RFC 1950 (zlib), Microsoft OOXML / OLE / EML specs.
_PDF = b"%PDF-"
_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_GIF87 = b"GIF87a"
_GIF89 = b"GIF89a"
_RIFF = b"RIFF"
_WEBP = b"WEBP"
_TIFF_LE = b"II*\x00"
_TIFF_BE = b"MM\x00*"
_BMP = b"BM"
_ZIP = b"PK\x03\x04"
_ZIP_EMPTY = b"PK\x05\x06"
_ZIP_SPANNED = b"PK\x07\x08"
_GZ = b"\x1f\x8b"
_7Z = b"7z\xbc\xaf\x27\x1c"
_TAR_USTAR = b"ustar"  # at offset 257
_OLE_CFB = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy .doc/.xls/.ppt and .msg
_HEIF_FTYP_OFFSET = 4
_HEIF_BRANDS = {
    b"heic",
    b"heix",
    b"hevc",
    b"hevx",
    b"heim",
    b"heis",
    b"hevm",
    b"hevs",
    b"mif1",
    b"msf1",
    b"avif",
}
# Plain-text-ish formats sniffed by content rather than magic.
_SVG_HINTS = (b"<svg", b"<?xml")
_HTML_HINTS = (b"<!doctype html", b"<html", b"<HTML", b"<!DOCTYPE HTML")
_EML_HEADERS = (b"Received:", b"From:", b"Return-Path:", b"MIME-Version:", b"Message-ID:")


def sniff_media_type(data: bytes, *, default: str | None = None, filename: str | None = None) -> str:
    """Best-effort content-type detection from magic bytes + filename hint.

    The filename hint disambiguates ZIP-based formats (DOCX, XLSX, PPTX,
    ODT all share the ZIP magic) and picks up text-ish formats whose
    bytes don't have unique magic.

    Returns the *normalized* MIME (lowercased, no parameters). Falls back
    to ``default`` (also normalized) or ``application/octet-stream``.
    """
    if not data:
        return _normalize(default)

    # Binary magic, longest-prefix-first to avoid false positives.
    if data.startswith(_PDF):
        return "application/pdf"
    if data.startswith(_PNG):
        return "image/png"
    if data.startswith(_JPEG):
        return "image/jpeg"
    if data.startswith(_GIF87) or data.startswith(_GIF89):
        return "image/gif"
    if data[:4] == _RIFF and data[8:12] == _WEBP:
        return "image/webp"
    if data.startswith(_TIFF_LE) or data.startswith(_TIFF_BE):
        return "image/tiff"
    if data.startswith(_BMP):
        return "image/bmp"
    if data.startswith(_7Z):
        return "application/x-7z-compressed"
    if data.startswith(_GZ):
        return "application/gzip"
    if len(data) >= 12 and data[_HEIF_FTYP_OFFSET : _HEIF_FTYP_OFFSET + 4] == b"ftyp":
        brand = data[8:12].lower()
        if brand in _HEIF_BRANDS:
            return "image/avif" if brand == b"avif" else "image/heic"
    if data.startswith(_OLE_CFB):
        # Legacy .doc/.xls/.ppt or Outlook .msg. Filename disambiguates.
        if filename:
            ext = _ext(filename)
            if ext == "msg":
                return "application/vnd.ms-outlook"
            if ext == "doc":
                return "application/msword"
            if ext == "xls":
                return "application/vnd.ms-excel"
            if ext == "ppt":
                return "application/vnd.ms-powerpoint"
        return "application/x-ole-compound"
    if data.startswith(_ZIP) or data.startswith(_ZIP_EMPTY) or data.startswith(_ZIP_SPANNED):
        # Distinguish OOXML / ODF / EPUB / plain ZIP via filename ext.
        ext = _ext(filename) if filename else ""
        ooxml = {
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "odt": "application/vnd.oasis.opendocument.text",
            "ods": "application/vnd.oasis.opendocument.spreadsheet",
            "odp": "application/vnd.oasis.opendocument.presentation",
            "epub": "application/epub+zip",
        }
        if ext in ooxml:
            return ooxml[ext]
        return "application/zip"
    # TAR magic lives at offset 257.
    if len(data) >= 265 and data[257:262] == _TAR_USTAR:
        return "application/x-tar"

    # Text-ish heuristics on the first kilobyte.
    head = data[:1024].lstrip()
    head_lower = head.lower()
    if any(head_lower.startswith(h.lower()) for h in _SVG_HINTS) and b"<svg" in head_lower:
        return "image/svg+xml"
    if any(head_lower.startswith(h.lower()) for h in _HTML_HINTS):
        return "text/html"
    if any(h.lower() in head_lower for h in _EML_HEADERS) and b"\n\n" in data[:8192]:
        return "message/rfc822"

    # Filename extension as last resort -- recognised aliases only.
    if filename:
        ext = _ext(filename)
        ext_map = {
            "pdf": "application/pdf",
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "webp": "image/webp",
            "tif": "image/tiff",
            "tiff": "image/tiff",
            "bmp": "image/bmp",
            "heic": "image/heic",
            "heif": "image/heic",
            "avif": "image/avif",
            "svg": "image/svg+xml",
            "html": "text/html",
            "htm": "text/html",
            "txt": "text/plain",
            "md": "text/markdown",
            "csv": "text/csv",
            "rtf": "application/rtf",
            "eml": "message/rfc822",
            "msg": "application/vnd.ms-outlook",
            "zip": "application/zip",
            "7z": "application/x-7z-compressed",
            "tar": "application/x-tar",
            "gz": "application/gzip",
            "tgz": "application/gzip",
        }
        if ext in ext_map:
            return ext_map[ext]
    return _normalize(default)


def _normalize(value: str | None) -> str:
    if not value:
        return "application/octet-stream"
    return value.lower().split(";")[0].strip() or "application/octet-stream"


def _ext(filename: str | None) -> str:
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[1].lower()
