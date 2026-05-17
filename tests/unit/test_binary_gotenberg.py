# Copyright 2026 Firefly Software Solutions Inc
"""``GotenbergConverter`` -- HTTP adapter mocked via respx."""

from __future__ import annotations

import httpx
import pytest
import respx

from flydocs.config import IDPSettings
from flydocs.core.services.binary.errors import OfficeConversionError
from flydocs.core.services.binary.gotenberg import GotenbergConverter

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _settings() -> IDPSettings:
    return IDPSettings(gotenberg_url="http://gotenberg:3000", gotenberg_timeout_s=5)


@pytest.mark.asyncio
@respx.mock
async def test_posts_to_libreoffice_endpoint_for_docx() -> None:
    route = respx.post("http://gotenberg:3000/forms/libreoffice/convert").mock(
        return_value=httpx.Response(200, content=b"%PDF-fake")
    )
    out = await GotenbergConverter(_settings()).convert(
        b"docx-bytes", media_type=_DOCX, filename="report.docx"
    )
    assert out == b"%PDF-fake"
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_posts_to_chromium_endpoint_for_html() -> None:
    route = respx.post("http://gotenberg:3000/forms/chromium/convert/html").mock(
        return_value=httpx.Response(200, content=b"%PDF-html")
    )
    out = await GotenbergConverter(_settings()).convert(
        b"<html></html>", media_type="text/html", filename="page.html"
    )
    assert out == b"%PDF-html"
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_4xx_raises_typed_error() -> None:
    respx.post("http://gotenberg:3000/forms/libreoffice/convert").mock(
        return_value=httpx.Response(400, text="bad input")
    )
    with pytest.raises(OfficeConversionError) as ei:
        await GotenbergConverter(_settings()).convert(b"x", media_type=_DOCX, filename="r.docx")
    assert "400" in str(ei.value)


@pytest.mark.asyncio
@respx.mock
async def test_network_error_raises_typed_error() -> None:
    respx.post("http://gotenberg:3000/forms/libreoffice/convert").mock(side_effect=httpx.ConnectError("nope"))
    with pytest.raises(OfficeConversionError):
        await GotenbergConverter(_settings()).convert(b"x", media_type=_DOCX, filename="r.docx")
