# Copyright 2026 Firefly Software Solutions Inc
"""``RequestValidator`` -- semantic preflight checks on an ExtractionRequest.

Pydantic validates the *shape* of the payload (types, required fields,
``min_length``). This validator catches the *semantic* mistakes a caller
can make even with a well-formed JSON body:

  * a ``RuleSpec.parents`` entry that points to a ``documentType`` not
    declared in ``docs``,
  * field names that don't exist in the referenced DocSpec,
  * a rule that depends on a validator name not declared on that doc,
  * a rule that depends on another rule's ``id`` not present in the request,
  * duplicate rule ids,
  * cycles in the rule DAG (detected before the rule engine is invoked),
  * ``stages.rule_engine`` toggled without any rule.

Each issue carries a ``severity`` (``error`` or ``warning``); the
controller layer raises ``422 invalid_request`` with the RFC 7807
problem-detail body when at least one error is present. Warnings are
returned but don't block the request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from typing import Literal

from flydesk_idp.interfaces.dtos.extract import ExtractionRequest
from flydesk_idp.interfaces.dtos.rule import (
    RuleFieldParent,
    RuleRuleParent,
    RuleValidatorParent,
)


Severity = Literal["error", "warning"]


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A single semantic problem with the incoming request."""

    severity: Severity
    code: str
    message: str
    path: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "path": self.path,
        }


@dataclass(slots=True)
class ValidationReport:
    """Aggregated result of validating an ExtractionRequest."""

    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    def to_payload(self) -> dict[str, list[dict[str, str]]]:
        return {
            "errors": [i.to_dict() for i in self.errors],
            "warnings": [i.to_dict() for i in self.warnings],
        }


class RequestValidator:
    """Pre-flight checks before the pipeline runs.

    Stateless and side-effect-free. Registered as a pyfly @bean so the
    controllers, the job submit handler, and the test suite all share
    one instance.
    """

    def validate(self, request: ExtractionRequest) -> ValidationReport:
        report = ValidationReport()
        self._check_files(request, report)
        self._check_docs(request, report)
        self._check_rule_references(request, report)
        self._check_rule_dag(request, report)
        self._check_stage_consistency(request, report)
        return report

    # -- file-level checks (multi-file shape) ----------------------------

    def _check_files(self, request: ExtractionRequest, report: ValidationReport) -> None:
        known_types = {d.docType.documentType for d in request.docs}
        for f_index, file in enumerate(request.documents):
            if not file.document_type:
                continue
            if file.document_type not in known_types:
                report.issues.append(ValidationIssue(
                    severity="error",
                    code="document_type_unknown",
                    message=(
                        f"File {file.filename!r} pins document_type "
                        f"{file.document_type!r} which is not declared in "
                        "docs[]."
                    ),
                    path=f"documents[{f_index}].document_type",
                ))

    # -- doc-level checks ------------------------------------------------

    def _check_docs(self, request: ExtractionRequest, report: ValidationReport) -> None:
        # Pydantic already rejects empty docs[] (min_length=1) but each
        # DocSpec may still have an empty fieldGroups or empty fieldFields.
        seen_doctypes: dict[str, int] = {}
        for d_index, doc in enumerate(request.docs):
            path = f"docs[{d_index}].docType.documentType"
            doc_type = doc.docType.documentType
            seen_doctypes[doc_type] = seen_doctypes.get(doc_type, 0) + 1
            if not doc.fieldGroups:
                report.issues.append(ValidationIssue(
                    severity="error",
                    code="empty_field_groups",
                    message=f"DocSpec {doc_type!r} declares no fieldGroups -- nothing to extract.",
                    path=f"docs[{d_index}].fieldGroups",
                ))
                continue
            for g_index, group in enumerate(doc.fieldGroups):
                if not group.fieldGroupFields:
                    report.issues.append(ValidationIssue(
                        severity="error",
                        code="empty_field_group",
                        message=(
                            f"DocSpec {doc_type!r} fieldGroup "
                            f"{group.fieldGroupName!r} has no fields."
                        ),
                        path=f"docs[{d_index}].fieldGroups[{g_index}].fieldGroupFields",
                    ))
            # Duplicate field names within the same doc.
            all_names: list[str] = [
                f.fieldName
                for g in doc.fieldGroups
                for f in g.fieldGroupFields
            ]
            seen: set[str] = set()
            for name in all_names:
                if name in seen:
                    report.issues.append(ValidationIssue(
                        severity="error",
                        code="duplicate_field_name",
                        message=(
                            f"DocSpec {doc_type!r} declares fieldName {name!r} "
                            "more than once."
                        ),
                        path=path,
                    ))
                seen.add(name)

        # Duplicate documentType across docs.
        for doc_type, count in seen_doctypes.items():
            if count > 1:
                report.issues.append(ValidationIssue(
                    severity="error",
                    code="duplicate_document_type",
                    message=(
                        f"documentType {doc_type!r} declared {count} times in "
                        "docs[]; document types must be unique."
                    ),
                    path="docs[].docType.documentType",
                ))

    # -- rule reference checks -------------------------------------------

    def _check_rule_references(
        self, request: ExtractionRequest, report: ValidationReport
    ) -> None:
        # Catalog what's declared so rule parents can be resolved.
        doc_index = {
            doc.docType.documentType: doc for doc in request.docs
        }
        fields_per_doc: dict[str, set[str]] = {
            dt: {f.fieldName for g in d.fieldGroups for f in g.fieldGroupFields}
            for dt, d in doc_index.items()
        }
        validators_per_doc: dict[str, set[str]] = {
            dt: {v.name for v in d.validators.visual}
            for dt, d in doc_index.items()
        }
        rule_ids = {r.id for r in request.rules}

        seen_ids: set[str] = set()
        for r_index, rule in enumerate(request.rules):
            if rule.id in seen_ids:
                report.issues.append(ValidationIssue(
                    severity="error",
                    code="duplicate_rule_id",
                    message=f"Rule id {rule.id!r} is declared more than once.",
                    path=f"rules[{r_index}].id",
                ))
            seen_ids.add(rule.id)

            for p_index, parent in enumerate(rule.parents):
                parent_path = f"rules[{r_index}].parents[{p_index}]"

                if isinstance(parent, RuleFieldParent):
                    if parent.documentType not in doc_index:
                        report.issues.append(ValidationIssue(
                            severity="error",
                            code="rule_unknown_doctype",
                            message=(
                                f"Rule {rule.id!r} references documentType "
                                f"{parent.documentType!r} which is not declared in docs[]."
                            ),
                            path=parent_path,
                        ))
                        continue
                    known = fields_per_doc.get(parent.documentType, set())
                    for fn in parent.fieldNames:
                        if fn not in known:
                            report.issues.append(ValidationIssue(
                                severity="error",
                                code="rule_unknown_field",
                                message=(
                                    f"Rule {rule.id!r} references field "
                                    f"{fn!r} on documentType "
                                    f"{parent.documentType!r}, but that doc "
                                    "doesn't declare such a field."
                                ),
                                path=parent_path,
                            ))

                elif isinstance(parent, RuleValidatorParent):
                    if parent.documentType not in doc_index:
                        report.issues.append(ValidationIssue(
                            severity="error",
                            code="rule_unknown_doctype",
                            message=(
                                f"Rule {rule.id!r} references documentType "
                                f"{parent.documentType!r} which is not declared in docs[]."
                            ),
                            path=parent_path,
                        ))
                        continue
                    known = validators_per_doc.get(parent.documentType, set())
                    if parent.validatorName not in known:
                        report.issues.append(ValidationIssue(
                            severity="error",
                            code="rule_unknown_validator",
                            message=(
                                f"Rule {rule.id!r} references validator "
                                f"{parent.validatorName!r} on documentType "
                                f"{parent.documentType!r}, but that doc "
                                "doesn't declare such a visual validator."
                            ),
                            path=parent_path,
                        ))

                elif isinstance(parent, RuleRuleParent):
                    if parent.ruleId not in rule_ids:
                        report.issues.append(ValidationIssue(
                            severity="error",
                            code="rule_unknown_parent",
                            message=(
                                f"Rule {rule.id!r} declares parent rule "
                                f"{parent.ruleId!r} which is not present in "
                                "the request."
                            ),
                            path=parent_path,
                        ))
                    elif parent.ruleId == rule.id:
                        report.issues.append(ValidationIssue(
                            severity="error",
                            code="rule_self_reference",
                            message=f"Rule {rule.id!r} declares itself as a parent.",
                            path=parent_path,
                        ))

    # -- DAG cycle check -------------------------------------------------

    def _check_rule_dag(
        self, request: ExtractionRequest, report: ValidationReport
    ) -> None:
        rule_ids = {r.id for r in request.rules}
        sorter: TopologicalSorter[str] = TopologicalSorter()
        for rule in request.rules:
            parents = [
                p.ruleId for p in rule.parents
                if isinstance(p, RuleRuleParent) and p.ruleId in rule_ids
            ]
            sorter.add(rule.id, *parents)
        try:
            sorter.prepare()
        except CycleError as exc:
            cycle = list(exc.args[1]) if len(exc.args) > 1 else []
            report.issues.append(ValidationIssue(
                severity="error",
                code="rule_cycle",
                message=(
                    "Rule graph contains a cycle: "
                    + (" -> ".join(cycle) if cycle else "(unknown path)")
                ),
                path="rules[]",
            ))

    # -- stage / toggle consistency --------------------------------------

    def _check_stage_consistency(
        self, request: ExtractionRequest, report: ValidationReport
    ) -> None:
        stages = request.options.stages

        # rule_engine on but no rules => the stage is a no-op. Warn.
        if stages.rule_engine and not request.rules:
            report.issues.append(ValidationIssue(
                severity="warning",
                code="rule_engine_no_rules",
                message=(
                    "stages.rule_engine is enabled but no rules are declared "
                    "-- the stage will run as a no-op."
                ),
                path="options.stages.rule_engine",
            ))

        # visual_authenticity on but no visual validators anywhere => warn.
        if stages.visual_authenticity:
            any_visual = any(
                bool(d.validators.visual) for d in request.docs
            )
            if not any_visual:
                report.issues.append(ValidationIssue(
                    severity="warning",
                    code="visual_authenticity_no_validators",
                    message=(
                        "stages.visual_authenticity is enabled but no "
                        "DocSpec declares visual validators."
                    ),
                    path="options.stages.visual_authenticity",
                ))

        # splitter on but only one doc => the stage will short-circuit. Warn.
        if stages.splitter and len(request.docs) <= 1:
            report.issues.append(ValidationIssue(
                severity="warning",
                code="splitter_single_doc",
                message=(
                    "stages.splitter is enabled but the request declares "
                    "only one DocSpec -- the splitter will short-circuit."
                ),
                path="options.stages.splitter",
            ))
