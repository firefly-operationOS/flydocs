# Copyright 2026 Firefly Software Solutions Inc
"""Per-file LLM document classifier."""

from flydesk_idp.core.services.classification.classifier import (
    UNMATCHED,
    ClassificationResult,
    DocumentClassifier,
)

__all__ = ["UNMATCHED", "ClassificationResult", "DocumentClassifier"]
