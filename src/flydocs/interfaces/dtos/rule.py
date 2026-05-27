# Copyright 2026 Firefly Software Solutions Inc
"""Business-rule DTOs.

Rules express boolean / categorical decisions over extracted fields,
validator outcomes, and other rules' results. They form a DAG; cycles are
rejected at request validation time by ``RequestValidator`` /
``RuleEngine``.

The :class:`RuleParent` discriminator is ``kind`` (not ``type``) to avoid
collision with :class:`Field.type` / :class:`RuleOutputSpec.type` when
walking a request by literal key name.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _BaseParent(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuleFieldParent(_BaseParent):
    kind: Literal["field"] = "field"
    document_type: str
    fields: list[str] = Field(..., min_length=1)


class RuleValidatorParent(_BaseParent):
    kind: Literal["validator"] = "validator"
    document_type: str
    validator: str


class RuleRuleParent(_BaseParent):
    kind: Literal["rule"] = "rule"
    rule: str


RuleParent = Annotated[
    RuleFieldParent | RuleValidatorParent | RuleRuleParent,
    Field(discriminator="kind"),
]


class RuleOutputSpec(BaseModel):
    """How the rule's output is interpreted."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(default="boolean", description="'boolean' | 'string' | 'number'.")
    valid_outputs: list[str] | None = None


class RuleSpec(BaseModel):
    """One business rule."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    predicate: str = Field(..., min_length=1)
    parents: list[RuleParent] = Field(default_factory=list)
    output: RuleOutputSpec = Field(default_factory=RuleOutputSpec)


class RuleResult(BaseModel):
    """Per-rule outcome returned in the response."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    predicate: str
    output: str = ""
    summary: str | None = None
    notes: list[str] = Field(default_factory=list)
    human_revision: str | None = None
