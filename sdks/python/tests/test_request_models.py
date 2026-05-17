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

"""Tests for the typed request-side models.

These verify two things:

1. Constructing a request with the typed sub-models produces the JSON
   the service expects on the wire (camelCase keys where required,
   snake_case for the rest).
2. The :class:`ExtractionRequest` / :class:`SubmitJobRequest` envelopes
   still accept the legacy dict-based form so callers can mix-and-match
   while migrating.
"""

from __future__ import annotations

from flydocs_sdk import (
    DocSpec,
    DocType,
    DocumentInput,
    ExtractionOptions,
    ExtractionRequest,
    FieldGroup,
    FieldSpec,
    FieldType,
    RuleFieldParent,
    RuleSpec,
    StageToggles,
    StandardFormat,
    StandardValidatorSpec,
    StandardValidatorType,
    SubmitJobRequest,
)


def test_field_spec_dumps_with_camelcase_keys() -> None:
    field = FieldSpec(
        field_name="total_amount",
        field_description="Total to pay",
        field_type=FieldType.NUMBER,
        required=True,
        minimum=0.0,
        standard_validators=[StandardValidatorSpec(type=StandardValidatorType.IBAN)],
    )
    dumped = field.model_dump(by_alias=True)
    # The service expects camelCase top-level keys for the field schema.
    assert dumped["name"] == "total_amount"
    assert dumped["description"] == "Total to pay"
    assert dumped["type"] == FieldType.NUMBER
    assert dumped["required"] is True
    assert dumped["minimum"] == 0.0
    assert dumped["standard_validators"][0]["type"] == "iban"


def test_field_group_factory_constructs_named_fields() -> None:
    group = FieldGroup.of(
        "totals",
        FieldSpec(field_name="total", field_type=FieldType.NUMBER, required=True),
        FieldSpec(field_name="currency", field_type=FieldType.STRING, required=True),
        description="Invoice totals block",
    )
    dumped = group.model_dump(by_alias=True)
    assert dumped["fieldGroupName"] == "totals"
    assert dumped["fieldGroupDesc"] == "Invoice totals block"
    assert len(dumped["fieldGroupFields"]) == 2


def test_doc_spec_round_trips() -> None:
    spec = DocSpec(
        doc_type=DocType(document_type="invoice", description="Vendor invoice"),
        field_groups=[
            FieldGroup.of(
                "totals",
                FieldSpec(field_name="total", field_type=FieldType.NUMBER, required=True),
            )
        ],
    )
    dumped = spec.model_dump(by_alias=True)
    assert dumped["docType"]["documentType"] == "invoice"
    assert dumped["fieldGroups"][0]["fieldGroupName"] == "totals"


def test_standard_format_round_trips() -> None:
    field = FieldSpec(field_name="dob", field_type=FieldType.STRING, format=StandardFormat.DATE)
    dumped = field.model_dump(by_alias=True)
    assert dumped["format"] == "date"


def test_rule_spec_round_trips_field_parent() -> None:
    rule = RuleSpec(
        id="total_matches_lines",
        predicate="Total equals the sum of line items",
        parents=[RuleFieldParent(document_type="invoice", field_names=["total", "line_items"])],
    )
    dumped = rule.model_dump(by_alias=True)
    assert dumped["id"] == "total_matches_lines"
    parent = dumped["parents"][0]
    assert parent["parentType"] == "field"
    assert parent["documentType"] == "invoice"
    assert parent["fieldNames"] == ["total", "line_items"]


def test_stage_toggles_defaults_match_service_defaults() -> None:
    s = StageToggles()
    assert s.splitter is False
    assert s.classifier is True
    assert s.field_validation is True
    assert s.judge is False
    assert s.bbox_refine is False


def test_extraction_options_typed() -> None:
    opts = ExtractionOptions(
        return_bboxes=True,
        language_hint="es",
        model="anthropic:claude-sonnet-4-6",
        stages=StageToggles(judge=True, bbox_refine=True),
        escalation_threshold=0.25,
        escalation_model="anthropic:claude-opus-4-7",
    )
    dumped = opts.model_dump(by_alias=True)
    assert dumped["language_hint"] == "es"
    assert dumped["stages"]["judge"] is True
    assert dumped["stages"]["bbox_refine"] is True
    assert dumped["escalation_threshold"] == 0.25


def test_extraction_request_accepts_typed_models() -> None:
    req = ExtractionRequest(
        documents=[DocumentInput.from_bytes(b"%PDF-1.4", filename="x.pdf")],
        docs=[
            DocSpec(
                doc_type=DocType(document_type="invoice"),
                field_groups=[
                    FieldGroup.of(
                        "totals",
                        FieldSpec(field_name="total", field_type=FieldType.NUMBER, required=True),
                    )
                ],
            )
        ],
        rules=[RuleSpec(id="r1", predicate="Total > 0")],
        options=ExtractionOptions(stages=StageToggles(bbox_refine=True)),
    )
    dumped = req.model_dump(by_alias=True, mode="json")
    # JSON keys must match the service's contract.
    assert dumped["documents"][0]["filename"] == "x.pdf"
    assert dumped["docs"][0]["docType"]["documentType"] == "invoice"
    assert dumped["rules"][0]["id"] == "r1"
    assert dumped["options"]["stages"]["bbox_refine"] is True


def test_extraction_request_still_accepts_dicts() -> None:
    # Forward-compat for callers who haven't migrated to the typed
    # request models yet.
    req = ExtractionRequest(
        documents=[DocumentInput.from_bytes(b"%PDF-1.4", filename="x.pdf")],
        docs=[
            {
                "docType": {"documentType": "invoice"},
                "fieldGroups": [{"fieldGroupName": "g", "fieldGroupFields": [{"name": "x"}]}],
            }
        ],
        options={"stages": {"bbox_refine": True}},
    )
    dumped = req.model_dump(by_alias=True, mode="json")
    assert dumped["docs"][0]["docType"]["documentType"] == "invoice"
    assert dumped["options"]["stages"]["bbox_refine"] is True


def test_submit_job_request_accepts_typed_models() -> None:
    req = SubmitJobRequest(
        documents=[DocumentInput.from_bytes(b"%PDF-1.4", filename="x.pdf")],
        docs=[
            DocSpec(
                doc_type=DocType(document_type="invoice"),
                field_groups=[
                    FieldGroup.of(
                        "totals",
                        FieldSpec(field_name="total", field_type=FieldType.NUMBER, required=True),
                    )
                ],
            )
        ],
        options=ExtractionOptions(stages=StageToggles(judge=True)),
        callback_url="https://example.com/webhook",
        metadata={"caller": "test"},
    )
    dumped = req.model_dump(by_alias=True, mode="json")
    assert dumped["callback_url"] == "https://example.com/webhook"
    assert dumped["options"]["stages"]["judge"] is True
    assert dumped["metadata"] == {"caller": "test"}
