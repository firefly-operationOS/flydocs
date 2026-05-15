# Copyright 2026 Firefly Software Solutions Inc
"""Public DTOs for the ``transform`` pipeline stage.

The transformation stage runs **after** every other LLM stage (extract,
judge, judge_escalation) and **before** ``rules`` / ``assemble``. It
lets callers express *post-extraction* logic without pushing it into
their own application code.

Two transformation types ship in-tree:

* :class:`EntityResolutionTransformation` -- declarative, free,
  millisecond-scale. Deduplicates rows of an array field group across
  documents using accent-fold + token-subset matching. The
  ``bastanteo-poderes-poc`` previously did this work outside the
  service; this stage subsumes it.
* :class:`LlmTransformation` -- free-form. Caller supplies an
  ``intention`` (a one-sentence goal in any language) and the engine
  runs a focused LLM call against the target group, returning a
  transformed list of rows in the same shape.

Both types are dispatched by
:class:`TransformationEngine` based on the discriminator ``type``.
Future declarative types (format normalisation, aggregation, role
mapping...) add to the discriminated union without changing the API.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class TransformationScope(StrEnum):
    """Whether a transformation applies per-document or across the whole request."""

    TASK = "task"
    """The transformation runs once per ``(segment, DocSpec)`` task and
    mutates that task's extracted groups in place. This is the right
    scope for transformations that only consider one document at a
    time (format normalisation, single-doc dedup)."""

    REQUEST = "request"
    """Groups with the matching ``fieldGroupName`` are concatenated
    across every task, the transformation runs once over the
    consolidated rows, and the result is emitted as a top-level
    ``request_transformations`` entry on :class:`ExtractionResult`.
    Use this for cross-document entity resolution: the same person
    appearing in multiple deeds collapses into a single canonical row."""


class _BaseTransformation(BaseModel):
    """Common fields every transformation carries."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target_group: str = Field(
        ...,
        description=(
            "``fieldGroupName`` the transformation operates on (for "
            "example ``personas``, ``line_items``). Must match a group "
            "that the extractor produces; the stage is a no-op if no "
            "matching group is found in the task."
        ),
    )
    output_group: str | None = Field(
        default=None,
        description=(
            "Optional new group name. When set, the original group is "
            "left untouched and the transformation output is appended "
            "as a new group; useful when you want both the raw and the "
            "transformed view in the response. When ``None`` (default), "
            "the target group is replaced in place."
        ),
    )
    scope: TransformationScope = TransformationScope.TASK


class EntityResolutionTransformation(_BaseTransformation):
    """Deterministic deduplication of an array field group's rows.

    The matcher operates in two phases:

    1. **DNI-first**: rows whose normalised DNI (NFKD-fold, alnum only,
       upper) collide are merged unconditionally.
    2. **Name-variant**: for rows without DNI, names are NFKD-folded
       and tokenised; two rows match when one token set is a subset
       of the other and they share at least ``min_shared_tokens``
       tokens. This handles ``"Andrés Contreras"`` vs
       ``"Andres Contreras Guillen"`` without merging unrelated
       people who happen to share a single first name.

    When rows are merged, the canonical row is built by picking the
    most complete value for each field (longest token-count for names,
    first non-empty for everything else).
    """

    type: Literal["entity_resolution"] = "entity_resolution"
    match_by: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Field names to consider for matching, in priority order. "
            "The first field in the list whose values are non-empty on "
            "both rows acts as the key. Typical: "
            "``['dni', 'nombre']`` -- match by DNI first, then by name "
            "for rows that lack DNI."
        ),
    )
    min_shared_tokens: int = Field(
        default=2,
        ge=1,
        description=(
            "Minimum shared name tokens required for a name-variant "
            "match. A single-token name (e.g. just ``Andrés``) is "
            "rarely unique enough to merge two rows; 2 is a safe "
            "default that bridges accent / surname variants without "
            "collapsing strangers."
        ),
    )


class LlmTransformation(_BaseTransformation):
    """Free-form LLM transformation of an array field group's rows.

    Use this for anything the declarative types can't do: role
    classification, summarisation, free-text normalisation, language
    translation, schema migration between extraction passes, etc.

    The transformer serialises the target group's rows to JSON, hands
    them to a focused LLM call with the caller's ``intention``, and
    expects the LLM to return a list of rows in the same shape. The
    response replaces (or, with ``output_group``, augments) the
    original group.
    """

    type: Literal["llm"] = "llm"
    intention: str = Field(
        ...,
        min_length=10,
        description=(
            "One-sentence goal in any language. Example: "
            '``"Normaliza cada cargo a una taxonomía cerrada: '
            '{administrador_unico, consejero, apoderado, otros}"``.'
        ),
    )
    prompt_id: str | None = Field(
        default=None,
        description=(
            "Optional named prompt template id from the catalog. When "
            "omitted, the default transform prompt is used and the "
            "``intention`` is interpolated into it."
        ),
    )


# Pydantic discriminated union. Adding a new declarative type later
# (e.g. ``FormatNormalisationTransformation``) is a single-line union
# extension here plus a new branch in
# :class:`TransformationEngine.apply`.
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
