# Copyright 2026 Firefly Software Solutions Inc
"""``RequestValidator`` -- semantic preflight checks on an ExtractionRequest.

Pydantic validates the *shape* of the payload (types, required fields,
``min_length``). This validator catches the *semantic* mistakes a caller
can make even with a well-formed JSON body:

  * a ``RuleSpec.parents`` entry that points to a ``document_type`` not
    declared in ``document_types``,
  * field names that don't exist in the referenced :class:`DocumentTypeSpec`,
  * a rule that depends on a validator name not declared on that document type,
  * a rule that depends on another rule's ``id`` not present in the request,
  * duplicate rule ids,
  * cycles in the rule DAG (detected before the rule engine is invoked),
  * ``stages.rule_engine`` toggled without any rule.

Each issue carries a ``severity`` (``error`` or ``warning``); the
controller layer raises ``422 validation_failed`` with the RFC 7807
problem-detail body when at least one error is present. Warnings are
returned but don't block the request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from typing import Literal

from flydocs.interfaces.dtos.extract import ExtractionRequest
from flydocs.interfaces.dtos.rule import (
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
    controllers, the extraction submit handler, and the test suite all
    share one instance.
    """

    def validate(self, request: ExtractionRequest) -> ValidationReport:
        report = ValidationReport()
        self._check_files(request, report)
        self._check_document_types(request, report)
        self._check_rule_references(request, report)
        self._check_rule_dag(request, report)
        self._check_stage_consistency(request, report)
        return report

    # -- file-level checks (multi-file shape) ----------------------------

    def _check_files(self, request: ExtractionRequest, report: ValidationReport) -> None:
        known_types = {d.id for d in request.document_types}
        for f_index, file in enumerate(request.files):
            if not file.expected_type:
                continue
            if file.expected_type not in known_types:
                report.issues.append(
                    ValidationIssue(
                        severity="error",
                        code="document_type_unknown",
                        message=(
                            f"File {file.filename!r} pins expected_type "
                            f"{file.expected_type!r} which is not declared in "
                            "document_types[]."
                        ),
                        path=f"files[{f_index}].expected_type",
                    )
                )

    # -- document-type-level checks --------------------------------------

    def _check_document_types(self, request: ExtractionRequest, report: ValidationReport) -> None:
        # Pydantic already rejects empty document_types[] (min_length=1)
        # but each DocumentTypeSpec may still have an empty field_groups
        # or empty group.fields.
        seen_doctypes: dict[str, int] = {}
        for d_index, doc in enumerate(request.document_types):
            path = f"document_types[{d_index}].id"
            doc_type = doc.id
            seen_doctypes[doc_type] = seen_doctypes.get(doc_type, 0) + 1
            if not doc.field_groups:
                report.issues.append(
                    ValidationIssue(
                        severity="error",
                        code="empty_field_groups",
                        message=(
                            f"DocumentTypeSpec {doc_type!r} declares no field_groups -- "
                            "nothing to extract."
                        ),
                        path=f"document_types[{d_index}].field_groups",
                    )
                )
                continue
            for g_index, group in enumerate(doc.field_groups):
                if not group.fields:
                    report.issues.append(
                        ValidationIssue(
                            severity="error",
                            code="empty_field_group",
                            message=(
                                f"DocumentTypeSpec {doc_type!r} field group {group.name!r} has no fields."
                            ),
                            path=f"document_types[{d_index}].field_groups[{g_index}].fields",
                        )
                    )
            # Duplicate field names within the same document type.
            all_names: list[str] = [f.name for g in doc.field_groups for f in g.fields]
            seen: set[str] = set()
            for name in all_names:
                if name in seen:
                    report.issues.append(
                        ValidationIssue(
                            severity="error",
                            code="duplicate_field_name",
                            message=(
                                f"DocumentTypeSpec {doc_type!r} declares field name {name!r} more than once."
                            ),
                            path=path,
                        )
                    )
                seen.add(name)

        # Duplicate document type id across declarations.
        for doc_type, count in seen_doctypes.items():
            if count > 1:
                report.issues.append(
                    ValidationIssue(
                        severity="error",
                        code="duplicate_document_type",
                        message=(
                            f"document type {doc_type!r} declared {count} times in "
                            "document_types[]; ids must be unique."
                        ),
                        path="document_types[].id",
                    )
                )

    # -- rule reference checks -------------------------------------------

    def _check_rule_references(self, request: ExtractionRequest, report: ValidationReport) -> None:
        # Catalog what's declared so rule parents can be resolved.
        doc_index = {doc.id: doc for doc in request.document_types}
        fields_per_doc: dict[str, set[str]] = {
            dt: {f.name for g in d.field_groups for f in g.fields}
            for dt, d in doc_index.items()
        }
        validators_per_doc: dict[str, set[str]] = {
            dt: {v.name for v in d.visual_checks} for dt, d in doc_index.items()
        }
        rule_ids = {r.id for r in request.rules}

        seen_ids: set[str] = set()
        for r_index, rule in enumerate(request.rules):
            if rule.id in seen_ids:
                report.issues.append(
                    ValidationIssue(
                        severity="error",
                        code="duplicate_rule_id",
                        message=f"Rule id {rule.id!r} is declared more than once.",
                        path=f"rules[{r_index}].id",
                    )
                )
            seen_ids.add(rule.id)

            for p_index, parent in enumerate(rule.parents):
                parent_path = f"rules[{r_index}].parents[{p_index}]"

                if isinstance(parent, RuleFieldParent):
                    if parent.document_type not in doc_index:
                        report.issues.append(
                            ValidationIssue(
                                severity="error",
                                code="rule_unknown_doctype",
                                message=(
                                    f"Rule {rule.id!r} references document_type "
                                    f"{parent.document_type!r} which is not declared in "
                                    "document_types[]."
                                ),
                                path=parent_path,
                            )
                        )
                        continue
                    known = fields_per_doc.get(parent.document_type, set())
                    for fn in parent.fields:
                        if fn not in known:
                            report.issues.append(
                                ValidationIssue(
                                    severity="error",
                                    code="rule_unknown_field",
                                    message=(
                                        f"Rule {rule.id!r} references field "
                                        f"{fn!r} on document_type "
                                        f"{parent.document_type!r}, but that document "
                                        "type doesn't declare such a field."
                                    ),
                                    path=parent_path,
                                )
                            )

                elif isinstance(parent, RuleValidatorParent):
                    if parent.document_type not in doc_index:
                        report.issues.append(
                            ValidationIssue(
                                severity="error",
                                code="rule_unknown_doctype",
                                message=(
                                    f"Rule {rule.id!r} references document_type "
                                    f"{parent.document_type!r} which is not declared in "
                                    "document_types[]."
                                ),
                                path=parent_path,
                            )
                        )
                        continue
                    known = validators_per_doc.get(parent.document_type, set())
                    if parent.validator not in known:
                        report.issues.append(
                            ValidationIssue(
                                severity="error",
                                code="rule_unknown_validator",
                                message=(
                                    f"Rule {rule.id!r} references validator "
                                    f"{parent.validator!r} on document_type "
                                    f"{parent.document_type!r}, but that document "
                                    "type doesn't declare such a visual check."
                                ),
                                path=parent_path,
                            )
                        )

                elif isinstance(parent, RuleRuleParent):
                    if parent.rule not in rule_ids:
                        report.issues.append(
                            ValidationIssue(
                                severity="error",
                                code="rule_unknown_parent",
                                message=(
                                    f"Rule {rule.id!r} declares parent rule "
                                    f"{parent.rule!r} which is not present in "
                                    "the request."
                                ),
                                path=parent_path,
                            )
                        )
                    elif parent.rule == rule.id:
                        report.issues.append(
                            ValidationIssue(
                                severity="error",
                                code="rule_self_reference",
                                message=f"Rule {rule.id!r} declares itself as a parent.",
                                path=parent_path,
                            )
                        )

    # -- DAG cycle check -------------------------------------------------

    def _check_rule_dag(self, request: ExtractionRequest, report: ValidationReport) -> None:
        rule_ids = {r.id for r in request.rules}
        sorter: TopologicalSorter[str] = TopologicalSorter()
        for rule in request.rules:
            parents = [
                p.rule for p in rule.parents if isinstance(p, RuleRuleParent) and p.rule in rule_ids
            ]
            sorter.add(rule.id, *parents)
        try:
            sorter.prepare()
        except CycleError as exc:
            cycle = list(exc.args[1]) if len(exc.args) > 1 else []
            report.issues.append(
                ValidationIssue(
                    severity="error",
                    code="rule_cycle",
                    message=(
                        "Rule graph contains a cycle: " + (" -> ".join(cycle) if cycle else "(unknown path)")
                    ),
                    path="rules[]",
                )
            )

    # -- stage / toggle consistency --------------------------------------

    def _check_stage_consistency(self, request: ExtractionRequest, report: ValidationReport) -> None:
        stages = request.options.stages

        # rule_engine on but no rules => the stage is a no-op. Warn.
        if stages.rule_engine and not request.rules:
            report.issues.append(
                ValidationIssue(
                    severity="warning",
                    code="rule_engine_no_rules",
                    message=(
                        "stages.rule_engine is enabled but no rules are declared "
                        "-- the stage will run as a no-op."
                    ),
                    path="options.stages.rule_engine",
                )
            )

        # visual_authenticity on but no visual_checks anywhere => warn.
        if stages.visual_authenticity:
            any_visual = any(bool(d.visual_checks) for d in request.document_types)
            if not any_visual:
                report.issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="visual_authenticity_no_validators",
                        message=(
                            "stages.visual_authenticity is enabled but no DocumentTypeSpec "
                            "declares visual_checks."
                        ),
                        path="options.stages.visual_authenticity",
                    )
                )

        # splitter on but only one document type => the stage will short-circuit. Warn.
        if stages.splitter and len(request.document_types) <= 1:
            report.issues.append(
                ValidationIssue(
                    severity="warning",
                    code="splitter_single_doc",
                    message=(
                        "stages.splitter is enabled but the request declares "
                        "only one DocumentTypeSpec -- the splitter will short-circuit."
                    ),
                    path="options.stages.splitter",
                )
            )
