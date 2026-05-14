# Copyright 2026 Firefly Software Solutions Inc
"""Business-rule DTOs.

Rules express boolean / categorical decisions over extracted fields,
validator outcomes, and other rules' results. They form a DAG: a rule
that depends on another rule's output is evaluated *after* the parent.
Cycles are rejected at request validation time by :class:`RuleEngine`.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class RuleFieldParent(BaseModel):
    parentType: Literal["field"] = "field"
    documentType: str
    fieldNames: list[str] = Field(..., min_length=1)


class RuleValidatorParent(BaseModel):
    parentType: Literal["validator"] = "validator"
    documentType: str
    validatorName: str


class RuleRuleParent(BaseModel):
    parentType: Literal["rule"] = "rule"
    ruleId: str


RuleParent = Annotated[
    RuleFieldParent | RuleValidatorParent | RuleRuleParent,
    Field(discriminator="parentType"),
]


class RuleOutputSpec(BaseModel):
    """How the rule's output is interpreted."""

    type: str = Field(default="boolean", description="``boolean``, ``string``, or ``number``.")
    valid_outputs: list[str] | None = Field(
        default=None,
        description=(
            "Optional closed set of valid output strings. The rule engine "
            "treats anything outside this set as ``flag_for_review``."
        ),
    )


class RuleSpec(BaseModel):
    """One business rule."""

    id: str = Field(..., min_length=1)
    predicate: str = Field(..., min_length=1, description="Natural-language predicate evaluated by the LLM.")
    parents: list[RuleParent] = Field(default_factory=list)
    output: RuleOutputSpec = Field(default_factory=RuleOutputSpec)


class RuleResult(BaseModel):
    """Per-rule outcome returned in the response."""

    rule_id: str
    predicate: str
    output: str = Field(
        default="", description="The resolved output value (string form -- ``true``/``false``/...)."
    )
    summary: str = ""
    notes: list[str] = Field(default_factory=list)
    human_revision: str = Field(default="", description="Instructions for a human reviewer if needed.")
