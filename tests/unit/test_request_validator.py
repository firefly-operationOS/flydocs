# Copyright 2026 Firefly Software Solutions Inc
"""Unit tests for :class:`RequestValidator` semantic checks."""

from __future__ import annotations

import base64

import pytest

from flydesk_idp.core.services.validation import RequestValidator
from flydesk_idp.interfaces.dtos.doc import DocSpec, DocType, ValidatorsSpec, VisualValidatorSpec
from flydesk_idp.interfaces.dtos.extract import (
    DocumentInput,
    ExtractionOptions,
    ExtractionRequest,
    StageToggles,
)
from flydesk_idp.interfaces.dtos.field import FieldGroup, FieldSpec
from flydesk_idp.interfaces.dtos.rule import (
    RuleFieldParent,
    RuleOutputSpec,
    RuleRuleParent,
    RuleSpec,
    RuleValidatorParent,
)
from flydesk_idp.interfaces.enums.field_type import FieldType

_DUMMY_B64 = base64.b64encode(b"%PDF-1.4 dummy").decode("ascii")


def _doc(doc_type: str = "passport", *, with_visual: bool = False) -> DocSpec:
    return DocSpec(
        docType=DocType(documentType=doc_type, description="x", country="ES"),
        fieldGroups=[
            FieldGroup(
                fieldGroupName="g",
                fieldGroupDesc="",
                fieldGroupFields=[
                    FieldSpec(fieldName="full_name", fieldDescription="x", fieldType=FieldType.STRING),
                    FieldSpec(fieldName="nif", fieldDescription="x", fieldType=FieldType.STRING),
                ],
            )
        ],
        validators=ValidatorsSpec(
            visual=[VisualValidatorSpec(name="photo_present", description="x")] if with_visual else []
        ),
    )


def _request(*, docs=None, rules=None, options=None) -> ExtractionRequest:
    return ExtractionRequest(
        documents=[
            DocumentInput(filename="x.pdf", content_base64=_DUMMY_B64, content_type="application/pdf")
        ],
        docs=docs or [_doc()],
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
            parents=[RuleFieldParent(parentType="field", documentType="passport", fieldNames=["full_name"])],
            output=RuleOutputSpec(type="boolean"),
        )
    ]
    report = validator.validate(_request(rules=rules))
    assert not report.has_errors


# -- error: rule references unknown docType ----------------------------------


def test_rule_unknown_doctype(validator: RequestValidator) -> None:
    rules = [
        RuleSpec(
            id="r1",
            predicate="x",
            parents=[RuleFieldParent(parentType="field", documentType="invoice", fieldNames=["foo"])],
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
            parents=[RuleFieldParent(parentType="field", documentType="passport", fieldNames=["nope"])],
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
            parents=[
                RuleValidatorParent(parentType="validator", documentType="passport", validatorName="missing")
            ],
        )
    ]
    report = validator.validate(_request(docs=[_doc()], rules=rules))
    codes = [i.code for i in report.errors]
    assert "rule_unknown_validator" in codes


def test_rule_validator_parent_ok_when_declared(validator: RequestValidator) -> None:
    rules = [
        RuleSpec(
            id="r1",
            predicate="x",
            parents=[
                RuleValidatorParent(
                    parentType="validator", documentType="passport", validatorName="photo_present"
                )
            ],
        )
    ]
    report = validator.validate(_request(docs=[_doc(with_visual=True)], rules=rules))
    assert not report.has_errors


# -- error: rule references unknown parent rule ------------------------------


def test_rule_unknown_parent_rule(validator: RequestValidator) -> None:
    rules = [
        RuleSpec(
            id="r1",
            predicate="x",
            parents=[RuleRuleParent(parentType="rule", ruleId="ghost")],
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
            parents=[RuleRuleParent(parentType="rule", ruleId="r1")],
        )
    ]
    report = validator.validate(_request(rules=rules))
    codes = [i.code for i in report.errors]
    assert "rule_self_reference" in codes or "rule_cycle" in codes


# -- error: rule DAG has a cycle ---------------------------------------------


def test_rule_cycle(validator: RequestValidator) -> None:
    rules = [
        RuleSpec(id="a", predicate="x", parents=[RuleRuleParent(parentType="rule", ruleId="b")]),
        RuleSpec(id="b", predicate="x", parents=[RuleRuleParent(parentType="rule", ruleId="a")]),
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


# -- error: duplicate docType across docs[] ----------------------------------


def test_duplicate_document_type(validator: RequestValidator) -> None:
    docs = [_doc("passport"), _doc("passport")]
    report = validator.validate(_request(docs=docs))
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


# -- warning: visual_authenticity on but no visual validators ----------------


def test_visual_auth_without_validators_is_warning_only(validator: RequestValidator) -> None:
    options = ExtractionOptions(stages=StageToggles(visual_authenticity=True))
    report = validator.validate(_request(docs=[_doc()], options=options))
    assert not report.has_errors
    codes = [i.code for i in report.warnings]
    assert "visual_authenticity_no_validators" in codes
