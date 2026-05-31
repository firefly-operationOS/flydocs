# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for the document loader's magic-byte sniff."""

from __future__ import annotations

import pytest

from flydocs.core.services.extraction.loader import load_document, sniff_media_type


def test_sniff_pdf() -> None:
    assert sniff_media_type(b"%PDF-1.4\n...") == "application/pdf"


def test_sniff_png() -> None:
    assert sniff_media_type(b"\x89PNG\r\n\x1a\n\x00\x00...") == "image/png"


def test_sniff_jpeg() -> None:
    assert sniff_media_type(b"\xff\xd8\xff\xe0...") == "image/jpeg"


def test_sniff_unknown_falls_back() -> None:
    assert sniff_media_type(b"random bytes here", default="image/heic") == "image/heic"


def test_load_document_rejects_empty() -> None:
    with pytest.raises(ValueError):
        load_document(b"", declared_media_type=None)


def test_load_document_uses_declared_type() -> None:
    doc = load_document(b"%PDF-1.4 minimal\n", declared_media_type="application/pdf")
    assert doc.media_type == "application/pdf"
    # pypdf will fail to parse the minimal blob and we default page_count to 1
    assert doc.page_count == 1
