# Copyright 2026 Firefly Software Solutions Inc
"""ArchiveUnpacker -- ZIP, TAR, GZIP expansion + limits."""

from __future__ import annotations

import gzip
import io
import tarfile
import zipfile

import pytest

from flydesk_idp.config import IDPSettings
from flydesk_idp.core.services.binary.archive import ArchiveUnpacker
from flydesk_idp.core.services.binary.errors import ArchiveExtractionError


def _settings(max_files: int = 50) -> IDPSettings:
    return IDPSettings(binary_max_expanded_files=max_files)


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)
    return buf.getvalue()


def _tar(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, payload in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def test_unpacks_zip() -> None:
    members = ArchiveUnpacker(_settings()).unpack(
        _zip({"a.pdf": b"%PDF-1.4\nbody", "b.png": b"\x89PNG\r\n\x1a\ndata"}),
        media_type="application/zip",
    )
    paths = sorted(p for p, _ in members)
    assert paths == ["a.pdf", "b.png"]


def test_unpacks_tar() -> None:
    members = ArchiveUnpacker(_settings()).unpack(
        _tar({"x.txt": b"hello", "y.pdf": b"%PDF-1.4\nx"}),
        media_type="application/x-tar",
    )
    assert {p for p, _ in members} == {"x.txt", "y.pdf"}


def test_unpacks_gzip_single_file() -> None:
    raw = b"%PDF-1.4\nhello"
    gz_bytes = gzip.compress(raw)
    members = ArchiveUnpacker(_settings()).unpack(
        gz_bytes, media_type="application/gzip", filename="report.pdf.gz"
    )
    assert len(members) == 1
    name, payload = members[0]
    assert payload == raw
    assert name == "report.pdf"


def test_zip_password_protected_member_raises() -> None:
    # ``zipfile`` cannot write encrypted ZIPs; flip the encryption bit
    # post-hoc on the local file header + central directory header so the
    # bytes look encrypted to a reader without us actually encrypting.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("locked.txt", b"clear bytes")
    raw = bytearray(buf.getvalue())
    # Local file header: signature PK\x03\x04, then version (2 bytes),
    # then general purpose flag (2 bytes) at offset 6.
    raw[6] |= 0x01
    # Central directory header: signature PK\x01\x02, then version (4 bytes),
    # then general purpose flag (2 bytes) at offset 8 from PK\x01\x02.
    cd_sig = raw.find(b"PK\x01\x02")
    if cd_sig != -1:
        raw[cd_sig + 8] |= 0x01
    with pytest.raises(ArchiveExtractionError) as ei:
        ArchiveUnpacker(_settings()).unpack(bytes(raw), media_type="application/zip")
    assert "password" in str(ei.value).lower()


def test_zip_corrupt_raises() -> None:
    with pytest.raises(ArchiveExtractionError):
        ArchiveUnpacker(_settings()).unpack(b"PK\x03\x04junk", media_type="application/zip")


def test_zip_fan_out_limit_enforced() -> None:
    members = {f"f{i}.txt": b"x" for i in range(10)}
    with pytest.raises(ArchiveExtractionError) as ei:
        ArchiveUnpacker(_settings(max_files=3)).unpack(_zip(members), media_type="application/zip")
    assert "max" in str(ei.value)
