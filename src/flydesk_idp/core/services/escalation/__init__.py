# Copyright 2026 Firefly Software Solutions Inc
"""Judge-driven escalation -- re-run with a stronger model on bad judgements."""

from flydesk_idp.core.services.escalation.judge_escalator import JudgeEscalator

__all__ = ["JudgeEscalator"]
