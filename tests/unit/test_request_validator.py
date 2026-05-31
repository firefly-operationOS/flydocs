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

"""Unit tests for :class:`RequestValidator` semantic checks."""

from __future__ import annotations

import base64

import pytest

from flydocs.core.services.validation import RequestValidator
from flydocs.interfaces.dtos.document_type import DocumentTypeSpec, VisualCheck
from flydocs.interfaces.dtos.extract import (
    ExtractionOptions,
    ExtractionRequest,
    FileInput,
    StageToggles,
)
from flydocs.interfaces.dtos.field import Field, FieldGroup
from flydocs.interfaces.dtos.rule import (
    RuleFieldParent,
    RuleOutputSpec,
    RuleRuleParent,
    RuleSpec,
    RuleValidatorParent,
)
from flydocs.interfaces.enums.field_type import FieldType

_DUMMY_B64 = base64.b64encode(b"%PDF-1.4 dummy").decode("ascii")


def _doc(doc_type: str = "passport", *, with_visual: bool = False) -> DocumentTypeSpec:
    return DocumentTypeSpec(
        id=doc_type,
        description="x",
        country="ES",
        field_groups=[
            FieldGroup(
                name="g",
                description="",
                fields=[
                    Field(name="full_name", description="x", type=FieldType.STRING),
                    Field(name="nif", description="x", type=FieldType.STRING),
                ],
            )
        ],
        visual_checks=[VisualCheck(name="photo_present", description="x")] if with_visual else [],
    )


def _request(*, document_types=None, rules=None, options=None) -> ExtractionRequest:
    return ExtractionRequest(
        files=[FileInput(filename="x.pdf", content_base64=_DUMMY_B64, content_type="application/pdf")],
        document_types=document_types or [_doc()],
        rules=rules or [],
        options=options or ExtractionOptions(),
    )


@pytest.fixture
def validator() -> RequestValidator:
    return RequestValidator()


# -- happy path --------------------------------------------------------------


def test_valid_minimal_request(validator: RequestValidator) -> None:
    """A vanilla single-doc request with no rules has no errors."""
    report = validator.validate(_request())
    assert not report.has_errors


def test_valid_rule_with_field_parent(validator: RequestValidator) -> None:
    rules = [
        RuleSpec(
            id="r1",
            predicate="full_name is set.",
            parents=[RuleFieldParent(kind="field", document_type="passport", fields=["full_name"])],
            output=RuleOutputSpec(type="boolean"),
        )
    ]
    report = validator.validate(_request(rules=rules))
    assert not report.has_errors


# -- error: rule references unknown document type ----------------------------


def test_rule_unknown_doctype(validator: RequestValidator) -> None:
    rules = [
        RuleSpec(
            id="r1",
            predicate="x",
            parents=[RuleFieldParent(kind="field", document_type="invoice", fields=["foo"])],
        )
    ]
    report = validator.validate(_request(rules=rules))
    assert report.has_errors
    codes = [i.code for i in report.errors]
    assert "rule_unknown_doctype" in codes


# -- error: rule references unknown field ------------------------------------


def test_rule_unknown_field(validator: RequestValidator) -> None:
    rules = [
        RuleSpec(
            id="r1",
            predicate="x",
            parents=[RuleFieldParent(kind="field", document_type="passport", fields=["nope"])],
        )
    ]
    report = validator.validate(_request(rules=rules))
    codes = [i.code for i in report.errors]
    assert "rule_unknown_field" in codes


# -- error: rule references unknown validator --------------------------------


def test_rule_unknown_validator(validator: RequestValidator) -> None:
    rules = [
        RuleSpec(
            id="r1",
            predicate="x",
            parents=[RuleValidatorParent(kind="validator", document_type="passport", validator="missing")],
        )
    ]
    report = validator.validate(_request(document_types=[_doc()], rules=rules))
    codes = [i.code for i in report.errors]
    assert "rule_unknown_validator" in codes


def test_rule_validator_parent_ok_when_declared(validator: RequestValidator) -> None:
    rules = [
        RuleSpec(
            id="r1",
            predicate="x",
            parents=[
                RuleValidatorParent(kind="validator", document_type="passport", validator="photo_present")
            ],
        )
    ]
    report = validator.validate(_request(document_types=[_doc(with_visual=True)], rules=rules))
    assert not report.has_errors


# -- error: rule references unknown parent rule ------------------------------


def test_rule_unknown_parent_rule(validator: RequestValidator) -> None:
    rules = [
        RuleSpec(
            id="r1",
            predicate="x",
            parents=[RuleRuleParent(kind="rule", rule="ghost")],
        )
    ]
    report = validator.validate(_request(rules=rules))
    codes = [i.code for i in report.errors]
    assert "rule_unknown_parent" in codes


# -- error: rule self-reference ----------------------------------------------


def test_rule_self_reference(validator: RequestValidator) -> None:
    rules = [
        RuleSpec(
            id="r1",
            predicate="x",
            parents=[RuleRuleParent(kind="rule", rule="r1")],
        )
    ]
    report = validator.validate(_request(rules=rules))
    codes = [i.code for i in report.errors]
    assert "rule_self_reference" in codes or "rule_cycle" in codes


# -- error: rule DAG has a cycle ---------------------------------------------


def test_rule_cycle(validator: RequestValidator) -> None:
    rules = [
        RuleSpec(id="a", predicate="x", parents=[RuleRuleParent(kind="rule", rule="b")]),
        RuleSpec(id="b", predicate="x", parents=[RuleRuleParent(kind="rule", rule="a")]),
    ]
    report = validator.validate(_request(rules=rules))
    codes = [i.code for i in report.errors]
    assert "rule_cycle" in codes


# -- error: duplicate rule id ------------------------------------------------


def test_duplicate_rule_id(validator: RequestValidator) -> None:
    rules = [
        RuleSpec(id="dup", predicate="x"),
        RuleSpec(id="dup", predicate="y"),
    ]
    report = validator.validate(_request(rules=rules))
    codes = [i.code for i in report.errors]
    assert "duplicate_rule_id" in codes


# -- error: duplicate document type across document_types[] ------------------


def test_duplicate_document_type(validator: RequestValidator) -> None:
    document_types = [_doc("passport"), _doc("passport")]
    report = validator.validate(_request(document_types=document_types))
    codes = [i.code for i in report.errors]
    assert "duplicate_document_type" in codes


# -- warning: rule_engine on but no rules ------------------------------------


def test_rule_engine_no_rules_is_warning_only(validator: RequestValidator) -> None:
    options = ExtractionOptions(stages=StageToggles(rule_engine=True))
    report = validator.validate(_request(options=options))
    assert not report.has_errors
    codes = [i.code for i in report.warnings]
    assert "rule_engine_no_rules" in codes


# -- warning: splitter on with single doc ------------------------------------


def test_splitter_single_doc_is_warning_only(validator: RequestValidator) -> None:
    options = ExtractionOptions(stages=StageToggles(splitter=True))
    report = validator.validate(_request(options=options))
    assert not report.has_errors
    codes = [i.code for i in report.warnings]
    assert "splitter_single_doc" in codes


# -- warning: visual_authenticity on but no visual checks --------------------


def test_visual_auth_without_validators_is_warning_only(validator: RequestValidator) -> None:
    options = ExtractionOptions(stages=StageToggles(visual_authenticity=True))
    report = validator.validate(_request(document_types=[_doc()], options=options))
    assert not report.has_errors
    codes = [i.code for i in report.warnings]
    assert "visual_authenticity_no_validators" in codes
