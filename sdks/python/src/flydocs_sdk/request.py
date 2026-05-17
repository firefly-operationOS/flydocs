# Copyright 2026 Firefly Software Solutions Inc
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Typed request-side models for the flydocs API.

These are the SDK's parallel of the service-side ``flydocs.interfaces.dtos``
tree, kept independent so installing ``flydocs-sdk`` does not pull in
the service runtime. The shapes are pinned to the on-wire JSON
contract — fields are named to match the keys the service expects,
camelCase keys (``fieldName``, ``fieldGroupFields``) are reproduced
verbatim, and snake_case keys (``content_base64``, ``submitted_at``)
are accepted via Pydantic aliases.

Forward-compatibility: every model declares ``extra="allow"`` so an
older SDK keeps round-tripping payloads that carry fields it does
not know about yet.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Common base
# ---------------------------------------------------------------------------


class _RequestBase(BaseModel):
    """Common config for every request-side model in the SDK.

    * ``extra="allow"`` — tolerate unknown fields.
    * ``populate_by_name=True`` — accept both Python snake_case and the
      JSON aliases (``fieldName`` / ``fieldGroupFields`` / …) that the
      service expects on the wire.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class FieldType(StrEnum):
    """Supported primitives for a :class:`FieldSpec`.

    Mirrors ``flydocs.interfaces.enums.field_type.FieldType``.
    """

    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    ARRAY = "array"


class StandardFormat(StrEnum):
    """JSON-Schema-style format hints applied by the field validator."""

    DATE = "date"
    DATE_TIME = "date-time"
    EMAIL = "email"
    URI = "uri"
    UUID = "uuid"


class StandardValidatorType(StrEnum):
    """Built-in field validators the service ships with.

    Mirrors ``flydocs.interfaces.enums.standard_validator.StandardValidatorType``
    member-for-member. New validators added on the service side will
    show up as unknown :class:`StrEnum` values; pass them as raw strings
    to :class:`StandardValidatorSpec.type` until the SDK ships a release
    that exposes them as enum members.
    """

    # --- network / web ---------------------------------------------------
    EMAIL = "email"
    URI = "uri"
    URL = "url"
    IPV4 = "ipv4"
    IPV6 = "ipv6"
    DOMAIN = "domain"
    SLUG = "slug"

    # --- temporal --------------------------------------------------------
    DATE = "date"
    DATETIME = "datetime"
    TIME = "time"
    ISO_8601 = "iso_8601"

    # --- identifiers -----------------------------------------------------
    UUID = "uuid"
    JSON = "json"
    HEX_COLOR = "hex_color"

    # --- finance ---------------------------------------------------------
    IBAN = "iban"
    BIC = "bic"
    CREDIT_CARD = "credit_card"
    CURRENCY_CODE = "currency_code"
    AMOUNT = "amount"

    # --- telephony -------------------------------------------------------
    PHONE_E164 = "phone_e164"

    # --- geographic ------------------------------------------------------
    COUNTRY_CODE = "country_code"
    LANGUAGE_CODE = "language_code"
    POSTAL_CODE = "postal_code"
    LATITUDE = "latitude"
    LONGITUDE = "longitude"

    # --- national identifiers -------------------------------------------
    NIF = "nif"
    NIE = "nie"
    CIF = "cif"
    VAT_ID = "vat_id"
    SSN = "ssn"
    PASSPORT_NUMBER = "passport_number"


# ---------------------------------------------------------------------------
# Standard validator
# ---------------------------------------------------------------------------


class StandardValidatorSpec(_RequestBase):
    """One built-in validator declaration attached to a :class:`FieldSpec`.

    StandardValidatorSpec(type=StandardValidatorType.IBAN)
    StandardValidatorSpec(type="phone_e164", params={"country": "ES"})
    StandardValidatorSpec(type="vat_id", params={"country": "ES"}, severity="warning")
    """

    type: StandardValidatorType
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Literal["error", "warning"] = "error"


# ---------------------------------------------------------------------------
# Pipeline options
# ---------------------------------------------------------------------------


class StageToggles(_RequestBase):
    """Opt-in switches for every optional pipeline stage.

    The multimodal extractor is always on; everything else is opt-in.
    Defaults match the service-side defaults so an empty
    :class:`StageToggles` produces the same behaviour as omitting
    the field.
    """

    splitter: bool = False
    classifier: bool = True
    field_validation: bool = True
    visual_authenticity: bool = False
    content_authenticity: bool = False
    judge: bool = False
    rule_engine: bool = False
    judge_escalation: bool = False
    bbox_refine: bool = False
    transform: bool = False


class ExtractionOptions(_RequestBase):
    """Per-request knobs.

    ``transformations`` and ``model``/``escalation_model`` are strings
    on purpose: model ids are arbitrary provider-specific tokens the
    service routes to the right backend.
    """

    return_bboxes: bool = True
    language_hint: str | None = Field(default=None, max_length=16)
    model: str | None = None
    declared_media_type: str | None = None
    stages: StageToggles = Field(default_factory=StageToggles)
    escalation_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    escalation_model: str | None = None
    transformations: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Post-extraction transformations applied by the ``transform`` "
            "stage. Each entry is the discriminated union from "
            "``flydocs.interfaces.dtos.transformation`` — kept as raw "
            "dicts so callers can pick the right shape without the SDK "
            "shipping the full transformation tree."
        ),
    )


# ---------------------------------------------------------------------------
# Field schema
# ---------------------------------------------------------------------------


class FieldItem(_RequestBase):
    """One sub-field declared inside an array field's ``items`` list."""

    field_name: str = Field(..., alias="fieldName", min_length=1)
    field_description: str = Field(default="", alias="fieldDescription")
    field_type: FieldType = Field(default=FieldType.STRING, alias="fieldType")
    pattern: str | None = None
    format: StandardFormat | None = None
    enum: list[Any] | None = None
    minimum: float | None = None
    maximum: float | None = None
    standard_validators: list[StandardValidatorSpec] = Field(default_factory=list)


class FieldSpec(_RequestBase):
    """One field the caller wants extracted.

    For ``field_type == FieldType.ARRAY`` use ``items`` to describe
    the repeating row's columns. The Pydantic aliases match the
    service's camelCase JSON keys so the dump round-trips with the
    ``flydocs.interfaces`` DTOs on the server side.

        FieldSpec(name="total_amount", type=FieldType.NUMBER, required=True)
        FieldSpec(
            name="line_items",
            type=FieldType.ARRAY,
            items=[
                FieldItem(field_name="description", field_type=FieldType.STRING),
                FieldItem(field_name="amount",      field_type=FieldType.NUMBER),
            ],
        )
    """

    field_name: str = Field(..., alias="name", min_length=1)
    field_description: str = Field(default="", alias="description")
    field_type: FieldType = Field(default=FieldType.STRING, alias="type")
    required: bool = False
    pattern: str | None = None
    format: StandardFormat | None = None
    enum: list[Any] | None = None
    minimum: float | None = None
    maximum: float | None = None
    items: list[FieldItem] | None = None
    standard_validators: list[StandardValidatorSpec] = Field(default_factory=list)


class FieldGroup(_RequestBase):
    """A named bundle of fields the service should extract together.

    FieldGroup(
        name="totals",
        fields=[
            FieldSpec(name="total_amount", type=FieldType.NUMBER, required=True),
            FieldSpec(name="currency",      type=FieldType.STRING),
        ],
    )
    """

    field_group_name: str = Field(..., alias="fieldGroupName", min_length=1)
    field_group_desc: str = Field(default="", alias="fieldGroupDesc")
    field_group_fields: list[FieldSpec] = Field(..., alias="fieldGroupFields", min_length=1)

    @classmethod
    def of(cls, name: str, *fields: FieldSpec, description: str = "") -> FieldGroup:
        """Concise factory: ``FieldGroup.of("totals", FieldSpec(...), FieldSpec(...))``."""
        return cls(
            field_group_name=name,
            field_group_desc=description,
            field_group_fields=list(fields),
        )


# ---------------------------------------------------------------------------
# Doc spec
# ---------------------------------------------------------------------------


class DocType(_RequestBase):
    document_type: str = Field(..., alias="documentType", min_length=1)
    description: str = ""
    country: str = Field(default="", max_length=2)


class VisualValidatorSpec(_RequestBase):
    """One visual check the service should run (e.g. signature presence)."""

    name: str = Field(..., min_length=1)
    description: str


class ValidatorsSpec(_RequestBase):
    visual: list[VisualValidatorSpec] = Field(default_factory=list)


class DocSpec(_RequestBase):
    """One expected document type plus its field schema.

    DocSpec(
        doc_type=DocType(document_type="invoice", description="Vendor invoice"),
        field_groups=[FieldGroup.of("totals", ...)],
    )
    """

    doc_type: DocType = Field(..., alias="docType")
    field_groups: list[FieldGroup] = Field(..., alias="fieldGroups", min_length=1)
    validators: ValidatorsSpec = Field(default_factory=ValidatorsSpec)


# ---------------------------------------------------------------------------
# Rule schema
# ---------------------------------------------------------------------------


class RuleFieldParent(_RequestBase):
    parent_type: Literal["field"] = Field(default="field", alias="parentType")
    document_type: str = Field(..., alias="documentType")
    field_names: list[str] = Field(..., alias="fieldNames", min_length=1)


class RuleValidatorParent(_RequestBase):
    parent_type: Literal["validator"] = Field(default="validator", alias="parentType")
    document_type: str = Field(..., alias="documentType")
    validator_name: str = Field(..., alias="validatorName")


class RuleRuleParent(_RequestBase):
    parent_type: Literal["rule"] = Field(default="rule", alias="parentType")
    rule_id: str = Field(..., alias="ruleId")


RuleParent = Annotated[
    RuleFieldParent | RuleValidatorParent | RuleRuleParent,
    Field(discriminator="parent_type"),
]


class RuleOutputSpec(_RequestBase):
    type: str = "boolean"
    valid_outputs: list[str] | None = None


class RuleSpec(_RequestBase):
    """One business rule expressed as a natural-language predicate over its parents.

    RuleSpec(
        id="invoice_total_matches",
        predicate="Total equals the sum of line items",
        parents=[RuleFieldParent(document_type="invoice", field_names=["total_amount", "line_items"])],
    )
    """

    id: str = Field(..., min_length=1)
    predicate: str = Field(..., min_length=1)
    parents: list[RuleParent] = Field(default_factory=list)
    output: RuleOutputSpec = Field(default_factory=RuleOutputSpec)


# ---------------------------------------------------------------------------
# Transformations
# ---------------------------------------------------------------------------


class TransformationScope(StrEnum):
    """Whether a transformation applies per-document or across the whole request.

    Mirrors ``flydocs.interfaces.dtos.transformation.TransformationScope``.
    """

    TASK = "task"
    """One pass per ``(segment, DocSpec)`` task. Use for single-document
    transformations (format normalisation, single-doc dedup)."""

    REQUEST = "request"
    """Groups with the matching ``target_group`` are concatenated across
    every task, the transformation runs once over the consolidated
    rows, and the result is emitted under
    ``ExtractionResult.request_transformations``."""


def entity_resolution(
    *,
    target_group: str,
    match_by: list[str],
    min_shared_tokens: int = 2,
    output_group: str | None = None,
    scope: TransformationScope = TransformationScope.TASK,
    id: str | None = None,
) -> dict[str, Any]:
    """Build an ``entity_resolution`` transformation payload.

    The matcher dedupes rows of an array field group by (1) exact-match
    on a normalised key (typically a DNI / VAT id) and (2) token-subset
    matching on names. See
    ``flydocs.interfaces.dtos.transformation.EntityResolutionTransformation``
    for the full algorithm.

    Returns a plain :class:`dict` ready for
    :attr:`ExtractionOptions.transformations`.
    """
    payload: dict[str, Any] = {
        "type": "entity_resolution",
        "target_group": target_group,
        "match_by": list(match_by),
        "min_shared_tokens": int(min_shared_tokens),
        "scope": scope.value,
    }
    if output_group is not None:
        payload["output_group"] = output_group
    if id is not None:
        payload["id"] = id
    return payload


def llm_transformation(
    *,
    target_group: str,
    intention: str,
    prompt_id: str | None = None,
    output_group: str | None = None,
    scope: TransformationScope = TransformationScope.TASK,
    id: str | None = None,
) -> dict[str, Any]:
    """Build a free-form ``llm`` transformation payload.

    ``intention`` is a one-sentence goal in any language describing what
    the LLM should do to the target group's rows.
    """
    if len(intention) < 10:
        raise ValueError("intention must be at least 10 characters")
    payload: dict[str, Any] = {
        "type": "llm",
        "target_group": target_group,
        "intention": intention,
        "scope": scope.value,
    }
    if prompt_id is not None:
        payload["prompt_id"] = prompt_id
    if output_group is not None:
        payload["output_group"] = output_group
    if id is not None:
        payload["id"] = id
    return payload


# ---------------------------------------------------------------------------
# Re-exports for callers that want one import line
# ---------------------------------------------------------------------------


__all__ = [
    "DocSpec",
    "DocType",
    "ExtractionOptions",
    "FieldGroup",
    "FieldItem",
    "FieldSpec",
    "FieldType",
    "RuleFieldParent",
    "RuleOutputSpec",
    "RuleParent",
    "RuleRuleParent",
    "RuleSpec",
    "RuleValidatorParent",
    "StageToggles",
    "StandardFormat",
    "StandardValidatorSpec",
    "StandardValidatorType",
    "TransformationScope",
    "ValidatorsSpec",
    "VisualValidatorSpec",
    "entity_resolution",
    "llm_transformation",
]
