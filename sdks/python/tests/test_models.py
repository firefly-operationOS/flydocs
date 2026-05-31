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

"""Unit tests for the v1 wire-level models.

Three things are pinned down here:

1. **Enum shape.** Every v1 enum is lowercase snake_case on the wire;
   the legacy v0 names (``QUEUED`` capitalised, ``PARTIAL_SUCCEEDED``,
   ``REFINING_BBOXES``) must not be present.
2. **Recursion.** A :class:`Field` can carry ``items`` (array row) and
   ``fields`` (object members); a roundtripped JSON dump preserves both
   levels.
3. **Forward compat.** Every model declares ``extra="allow"``, so unknown
   fields the service sends are preserved in ``model_extra`` rather than
   silently dropped or failing validation.
"""

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path

import pytest

from flydocs_sdk import (
    BboxQuality,
    BboxSource,
    BoundingBox,
    CheckStatus,
    ContentIntegrityStatus,
    DocumentTypeSpec,
    EventEnvelope,
    Extraction,
    ExtractionRequest,
    ExtractionResult,
    ExtractionStatus,
    Field,
    FieldGroup,
    FieldType,
    FileInput,
    JudgeStatus,
    PostProcessing,
    PostProcessingStatus,
    RuleFieldParent,
    RuleRuleParent,
    RuleSpec,
    StandardFormat,
    SubmitExtractionRequest,
    ValidationRule,
    ValidatorSpec,
    ValidatorType,
    VisualCheck,
)

PDF_B64 = base64.b64encode(b"%PDF-1.4\n").decode()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def test_extraction_status_lowercase_values() -> None:
    assert ExtractionStatus.QUEUED.value == "queued"
    assert ExtractionStatus.RUNNING.value == "running"
    assert ExtractionStatus.SUCCEEDED.value == "succeeded"
    assert ExtractionStatus.FAILED.value == "failed"
    assert ExtractionStatus.CANCELLED.value == "cancelled"


def test_extraction_status_drops_legacy_members() -> None:
    members = {m.name for m in ExtractionStatus}
    assert "PARTIAL_SUCCEEDED" not in members
    assert "REFINING_BBOXES" not in members
    assert members == {"QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"}


def test_post_processing_status_lowercase_values() -> None:
    assert PostProcessingStatus.PENDING.value == "pending"
    assert PostProcessingStatus.RUNNING.value == "running"
    assert PostProcessingStatus.SUCCEEDED.value == "succeeded"
    assert PostProcessingStatus.FAILED.value == "failed"


def test_extraction_status_is_terminal_helper() -> None:
    assert not ExtractionStatus.QUEUED.is_terminal
    assert not ExtractionStatus.RUNNING.is_terminal
    assert ExtractionStatus.SUCCEEDED.is_terminal
    assert ExtractionStatus.FAILED.is_terminal
    assert ExtractionStatus.CANCELLED.is_terminal


def test_judge_status_lowercase() -> None:
    assert JudgeStatus.PASS.value == "pass"
    assert JudgeStatus.FAIL.value == "fail"
    assert JudgeStatus.UNCERTAIN.value == "uncertain"


def test_check_status_lowercase() -> None:
    assert CheckStatus.PASS.value == "pass"


def test_content_integrity_status_lowercase() -> None:
    assert ContentIntegrityStatus.VALID.value == "valid"


def test_bbox_source_drops_none() -> None:
    members = {m.name for m in BboxSource}
    assert "NONE" not in members
    assert members == {"LLM", "PDF_TEXT", "OCR"}


def test_bbox_quality_drops_empty() -> None:
    members = {m.name for m in BboxQuality}
    assert "EMPTY" not in members
    assert members == {"GOOD", "POOR", "SUSPICIOUS", "INVALID"}


def test_field_type_object_added() -> None:
    assert FieldType.OBJECT.value == "object"


def test_standard_format_adds_time_and_currency() -> None:
    assert StandardFormat.TIME.value == "time"
    assert StandardFormat.CURRENCY.value == "currency"


def test_validation_rule_validator_replaces_standard() -> None:
    assert ValidationRule.VALIDATOR.value == "validator"
    members = {m.name for m in ValidationRule}
    assert "STANDARD" not in members


# ---------------------------------------------------------------------------
# FileInput
# ---------------------------------------------------------------------------


def test_file_input_basic() -> None:
    f = FileInput(filename="a.pdf", content_base64=PDF_B64, expected_type="invoice")
    assert f.filename == "a.pdf"
    assert f.expected_type == "invoice"


def test_file_input_from_bytes_roundtrip() -> None:
    f = FileInput.from_bytes(b"hello", filename="x.txt", content_type="text/plain")
    assert base64.b64decode(f.content_base64) == b"hello"
    assert f.filename == "x.txt"
    assert f.content_type == "text/plain"


def test_file_input_from_path(tmp_path: Path) -> None:
    p = tmp_path / "x.bin"
    p.write_bytes(b"abc")
    f = FileInput.from_path(p, expected_type="invoice")
    assert f.filename == "x.bin"
    assert base64.b64decode(f.content_base64) == b"abc"
    assert f.expected_type == "invoice"


def test_file_input_strips_data_url_prefix() -> None:
    f = FileInput(filename="x.pdf", content_base64="data:application/pdf;base64,YWJj")
    assert f.content_base64 == "YWJj"


# ---------------------------------------------------------------------------
# Recursive Field
# ---------------------------------------------------------------------------


def test_field_primitive() -> None:
    f = Field(name="total", type=FieldType.NUMBER, required=True, minimum=0.0)
    assert f.name == "total"
    assert f.type == FieldType.NUMBER
    assert f.items is None
    assert f.fields is None


def test_field_array_with_items() -> None:
    f = Field(
        name="line_items",
        type=FieldType.ARRAY,
        items=Field(
            name="row",
            type=FieldType.OBJECT,
            fields=[
                Field(name="description", type=FieldType.STRING),
                Field(name="amount", type=FieldType.NUMBER),
            ],
        ),
    )
    assert f.items is not None
    assert f.items.type == FieldType.OBJECT
    assert f.items.fields is not None
    assert len(f.items.fields) == 2


def test_field_object_with_fields() -> None:
    f = Field(
        name="address",
        type=FieldType.OBJECT,
        fields=[
            Field(name="street", type=FieldType.STRING),
            Field(name="zip", type=FieldType.STRING),
        ],
    )
    assert f.type == FieldType.OBJECT
    assert f.fields is not None
    assert len(f.fields) == 2


def test_field_dumps_recursive_shape() -> None:
    f = Field(
        name="rows",
        type=FieldType.ARRAY,
        items=Field(
            name="r",
            type=FieldType.OBJECT,
            fields=[Field(name="x", type=FieldType.STRING)],
        ),
    )
    dumped = f.model_dump(mode="json", by_alias=True)
    assert dumped["type"] == "array"
    assert dumped["items"]["type"] == "object"
    assert dumped["items"]["fields"][0]["name"] == "x"


def test_field_validators_list() -> None:
    f = Field(
        name="iban",
        validators=[ValidatorSpec(name=ValidatorType.IBAN)],
    )
    dumped = f.model_dump(mode="json")
    # The dispatch key is ``name`` in v1 (not ``type``).
    assert dumped["validators"][0]["name"] == "iban"


# ---------------------------------------------------------------------------
# DocumentTypeSpec
# ---------------------------------------------------------------------------


def test_document_type_spec_flat() -> None:
    spec = DocumentTypeSpec(
        id="invoice",
        description="Vendor invoice",
        country="ES",
        field_groups=[
            FieldGroup(
                name="header",
                fields=[Field(name="invoice_number", type=FieldType.STRING)],
            )
        ],
        visual_checks=[VisualCheck(name="signature_present", description="visible signature")],
    )
    dumped = spec.model_dump(mode="json")
    assert dumped["id"] == "invoice"
    assert dumped["description"] == "Vendor invoice"
    assert dumped["country"] == "ES"
    assert dumped["field_groups"][0]["name"] == "header"
    assert dumped["visual_checks"][0]["name"] == "signature_present"


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def test_rule_field_parent_uses_kind_discriminator() -> None:
    parent = RuleFieldParent(document_type="invoice", fields=["total", "currency"])
    dumped = parent.model_dump(mode="json")
    assert dumped["kind"] == "field"
    assert dumped["document_type"] == "invoice"
    assert dumped["fields"] == ["total", "currency"]


def test_rule_rule_parent_field_renamed() -> None:
    parent = RuleRuleParent(rule="other-rule-id")
    dumped = parent.model_dump(mode="json")
    assert dumped["kind"] == "rule"
    assert dumped["rule"] == "other-rule-id"


def test_rule_spec_roundtrip() -> None:
    rule = RuleSpec(
        id="totals_ok",
        predicate="subtotal + tax = total",
        parents=[RuleFieldParent(document_type="invoice", fields=["subtotal", "tax", "total"])],
    )
    dumped = rule.model_dump(mode="json")
    assert dumped["id"] == "totals_ok"
    assert dumped["parents"][0]["kind"] == "field"


# ---------------------------------------------------------------------------
# ExtractionRequest envelope
# ---------------------------------------------------------------------------


def test_extraction_request_uses_v1_keys() -> None:
    req = ExtractionRequest(
        files=[FileInput.from_bytes(b"%PDF-1.4", filename="x.pdf")],
        document_types=[
            DocumentTypeSpec(
                id="invoice",
                field_groups=[
                    FieldGroup(
                        name="g",
                        fields=[Field(name="x", type=FieldType.STRING)],
                    )
                ],
            )
        ],
    )
    dumped = req.model_dump(mode="json")
    assert "files" in dumped
    assert "documents" not in dumped
    assert "document_types" in dumped
    assert "docs" not in dumped
    assert "request_id" not in dumped


def test_submit_extraction_request_adds_callback_and_metadata() -> None:
    req = SubmitExtractionRequest(
        files=[FileInput.from_bytes(b"x", filename="a.pdf")],
        document_types=[
            DocumentTypeSpec(
                id="x",
                field_groups=[FieldGroup(name="g", fields=[Field(name="a", type=FieldType.STRING)])],
            )
        ],
        callback_url="https://example.com/wh",
        metadata={"caller": "test"},
    )
    dumped = req.model_dump(mode="json")
    assert dumped["callback_url"] == "https://example.com/wh"
    assert dumped["metadata"] == {"caller": "test"}


# ---------------------------------------------------------------------------
# Extraction lifecycle
# ---------------------------------------------------------------------------


def test_extraction_minimal_parse() -> None:
    ext = Extraction.model_validate(
        {
            "id": "ext_1",
            "status": "queued",
            "submitted_at": "2026-01-01T00:00:00Z",
        }
    )
    assert ext.id == "ext_1"
    assert ext.status == ExtractionStatus.QUEUED


def test_extraction_with_post_processing() -> None:
    ext = Extraction.model_validate(
        {
            "id": "ext_1",
            "status": "succeeded",
            "submitted_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:01:00Z",
            "post_processing": {
                "bbox_refinement": {
                    "status": "running",
                    "started_at": "2026-01-01T00:01:00Z",
                    "attempts": 1,
                }
            },
        }
    )
    assert ext.post_processing is not None
    assert ext.post_processing.bbox_refinement is not None
    assert ext.post_processing.bbox_refinement.status == PostProcessingStatus.RUNNING


def test_extraction_extra_allow() -> None:
    # Forward-compat: a new field shows up on the wire and the SDK
    # surfaces it via ``model_extra`` rather than failing validation.
    ext = Extraction.model_validate(
        {
            "id": "ext_1",
            "status": "queued",
            "submitted_at": "2026-01-01T00:00:00Z",
            "future_field": {"shiny": True},
        }
    )
    assert ext.model_extra is not None
    assert ext.model_extra["future_field"] == {"shiny": True}


# ---------------------------------------------------------------------------
# ExtractionResult
# ---------------------------------------------------------------------------


def test_extraction_result_pipeline_nested() -> None:
    result = ExtractionResult.model_validate(
        {
            "id": "ext_1",
            "status": "success",
            "files": [],
            "documents": [],
            "discovered_documents": [],
            "rule_results": [],
            "request_transformations": [],
            "pipeline": {
                "model": "anthropic:claude-sonnet-4-6",
                "latency_ms": 1234,
                "trace": [],
                "errors": [],
            },
        }
    )
    assert result.id == "ext_1"
    # In v1 the model + latency live under ``pipeline``, not at top level.
    assert result.pipeline.model == "anthropic:claude-sonnet-4-6"
    assert result.pipeline.latency_ms == 1234


def test_extraction_result_tolerates_unknown_fields() -> None:
    payload = {
        "id": "ext_1",
        "status": "success",
        "documents": [],
        "discovered_documents": [],
        "pipeline": {"model": "m", "latency_ms": 0},
        "future_top_level": {"shiny": True},
    }
    result = ExtractionResult.model_validate(payload)
    assert result.model_extra is not None
    assert result.model_extra["future_top_level"] == {"shiny": True}


# ---------------------------------------------------------------------------
# Bounding box
# ---------------------------------------------------------------------------


def test_bounding_box_construct() -> None:
    bbox = BoundingBox(xmin=0.1, ymin=0.2, xmax=0.5, ymax=0.6, source=BboxSource.PDF_TEXT)
    assert bbox.source == BboxSource.PDF_TEXT


# ---------------------------------------------------------------------------
# EventEnvelope
# ---------------------------------------------------------------------------


def test_event_envelope_parses_completed_event() -> None:
    body = {
        "event_id": "evt-1",
        "event_type": "extraction.completed",
        "version": "1.0.0",
        "occurred_at": "2026-01-01T00:00:00Z",
        "extraction": {
            "id": "ext_1",
            "status": "succeeded",
            "submitted_at": "2026-01-01T00:00:00Z",
        },
        "metadata": {"caller": "test"},
    }
    env = EventEnvelope.model_validate(body)
    assert env.event_type == "extraction.completed"
    assert env.extraction.status == ExtractionStatus.SUCCEEDED
    assert env.metadata == {"caller": "test"}


def test_event_envelope_defaults() -> None:
    # Constructing without explicit event_id / occurred_at fills in defaults.
    env = EventEnvelope(
        event_type="extraction.submitted",
        extraction=Extraction(
            id="ext_1",
            status=ExtractionStatus.QUEUED,
            submitted_at=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
        ),
    )
    assert env.event_id  # auto-generated
    assert env.occurred_at  # auto-generated


# ---------------------------------------------------------------------------
# PostProcessing
# ---------------------------------------------------------------------------


def test_post_processing_optional_bbox_refinement() -> None:
    pp = PostProcessing()
    assert pp.bbox_refinement is None


# ---------------------------------------------------------------------------
# Hard-fail enum sanity
# ---------------------------------------------------------------------------


def test_unknown_extraction_status_value_raises() -> None:
    with pytest.raises(ValueError):
        ExtractionStatus("running_zzz")
