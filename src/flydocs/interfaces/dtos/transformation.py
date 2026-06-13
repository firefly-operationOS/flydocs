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


class PartsOfWholeInvariant(BaseModel):
    """A caller-declared "the parts must sum to a whole" constraint on a transform.

    Domain-agnostic: name the per-row numeric ``share_field`` and the ``total``
    those shares must add up to (e.g. ``100`` for percentages, or a capital /
    headcount / budget figure for absolute counts). The engine does arithmetic
    only -- it has no notion of what the rows represent. After the LLM emits its
    rows the engine sums ``share_field`` and, when the total exceeds ``total``
    beyond ``tolerance``, either repairs it (drops the least-trustworthy /
    unsourced rows until it fits) or just warns, per ``on_violation``. An
    under-sum is never "repaired" by inventing rows -- it is left as-is.
    """

    model_config = ConfigDict(extra="forbid")

    share_field: str = Field(..., min_length=1)
    total: float = Field(default=100.0, gt=0.0)
    tolerance: float = Field(default=0.5, ge=0.0)
    on_violation: Literal["repair", "warn"] = "repair"


class LlmTransformation(_BaseTransformation):
    """Free-form LLM transformation of an array field group's rows."""

    type: Literal["llm"] = "llm"
    intention: str = Field(..., min_length=10)
    prompt_id: str | None = None
    # Surface the provenance the extractor already captured (per-row id, pages,
    # confidence, judge evidence) to the model so it reconciles on evidence and
    # can cite which input rows each output row derives from. Domain-agnostic.
    include_provenance: bool = True
    # Optional parts-of-whole guard enforced deterministically after the LLM call.
    invariant: PartsOfWholeInvariant | None = None


Transformation = Annotated[
    EntityResolutionTransformation | LlmTransformation,
    Field(discriminator="type"),
]


__all__ = [
    "EntityResolutionTransformation",
    "LlmTransformation",
    "PartsOfWholeInvariant",
    "Transformation",
    "TransformationScope",
]
