# Copyright 2026 Firefly Software Solutions Inc
"""EmailUnpacker -- EML attachments + body."""

from __future__ import annotations

from email.message import EmailMessage

from flydocs.core.services.binary.email import EmailUnpacker


def _eml_with_attachment() -> bytes:
    msg = EmailMessage()
    msg["From"] = "a@b.com"
    msg["To"] = "c@d.com"
    msg["Subject"] = "test"
    msg.set_content("body text here")
    msg.add_attachment(
        b"%PDF-1.4\nfake pdf",
        maintype="application",
        subtype="pdf",
        filename="passport.pdf",
    )
    return msg.as_bytes()


def test_eml_yields_attachment_and_text_body() -> None:
    items = EmailUnpacker().unpack(_eml_with_attachment(), media_type="message/rfc822")
    by_name = {name: payload for name, payload in items}
    assert "passport.pdf" in by_name
    assert by_name["passport.pdf"].startswith(b"%PDF-")
    assert "body.txt" in by_name
    assert b"body text here" in by_name["body.txt"]


def test_eml_without_attachments_yields_body_only() -> None:
    msg = EmailMessage()
    msg["From"] = "a@b.com"
    msg["Subject"] = "x"
    msg.set_content("body only")
    items = EmailUnpacker().unpack(msg.as_bytes(), media_type="message/rfc822")
    names = [name for name, _ in items]
    assert "body.txt" in names
    assert all(name != "passport.pdf" for name in names)
