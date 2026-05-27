# Copyright 2026 Firefly Software Solutions Inc
"""Public DTOs for the ``transform`` pipeline stage.

The transformation stage runs **after** every other LLM stage (extract,
judge, judge_escalation) and **before** rules / assemble. It lets callers
express post-extraction logic without pushing it into their own application
code.

Two transformation types ship in-tree:

* :class:`EntityResolutionTransformation` -- declarative, free,
  millisecond-scale. Deduplicates rows of an array/object field group
  across documents using accent-fold + token-subset matching.
* :class:`LlmTransformation` -- free-form. Caller supplies an
  ``intention`` (a one-sentence goal in any language) and the engine
  runs a focused LLM call against the target group, returning a
  transformed list of rows in the same shape.

The discriminator is ``type``. New declarative types extend the union.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class TransformationScope(StrEnum):
    """Whether a transformation applies per-document or across the whole request."""

    TASK = "task"
    REQUEST = "request"


class _BaseTransformation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target_group: str = Field(..., min_length=1)
    output_group: str | None = None
    scope: TransformationScope = TransformationScope.TASK


class EntityResolutionTransformation(_BaseTransformation):
    """Deterministic deduplication of an array field group's rows."""

    type: Literal["entity_resolution"] = "entity_resolution"
    match_by: list[str] = Field(..., min_length=1)
    min_shared_tokens: int = Field(default=2, ge=1)


class LlmTransformation(_BaseTransformation):
    """Free-form LLM transformation of an array field group's rows."""

    type: Literal["llm"] = "llm"
    intention: str = Field(..., min_length=10)
    prompt_id: str | None = None


Transformation = Annotated[
    EntityResolutionTransformation | LlmTransformation,
    Field(discriminator="type"),
]


__all__ = [
    "EntityResolutionTransformation",
    "LlmTransformation",
    "Transformation",
    "TransformationScope",
]
