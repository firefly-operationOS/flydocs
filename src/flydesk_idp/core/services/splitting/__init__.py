# Copyright 2026 Firefly Software Solutions Inc
"""LLM-driven document splitter -- maps target docTypes to page ranges."""

from flydesk_idp.core.services.splitting.splitter import (
    DocumentSplitter,
    SplitDocument,
    SplitResult,
)

__all__ = ["DocumentSplitter", "SplitDocument", "SplitResult"]
