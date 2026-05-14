# Copyright 2026 Firefly Software Solutions Inc
"""``RuleEngine`` -- DAG-aware LLM evaluator for business rules.

Rules are sorted topologically by their declared ``parents`` and
evaluated in levels: every rule whose parents are already resolved
runs in the current level, and its outputs feed the next level's
prompt. Cycles raise :class:`ValueError` at preparation time; an
empty ``rules`` list short-circuits to ``[]``. Prompt template
injected via DI.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from graphlib import CycleError, TopologicalSorter
from typing import Any

from fireflyframework_agentic.agents import FireflyAgent
from fireflyframework_agentic.prompts import PromptTemplate
from pydantic import BaseModel, Field

from flydesk_idp.interfaces.dtos.authenticity import VisualValidationOutcome
from flydesk_idp.interfaces.dtos.doc import DocSpec
from flydesk_idp.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup
from flydesk_idp.interfaces.dtos.rule import (
    RuleFieldParent,
    RuleResult,
    RuleRuleParent,
    RuleSpec,
    RuleValidatorParent,
)

logger = logging.getLogger(__name__)


class _RawRuleResult(BaseModel):
    rule_id: str
    predicate: str = ""
    output: str = ""
    summary: str = ""
    notes: list[str] = Field(default_factory=list)
    human_revision: str = ""


class _RuleEngineOutput(BaseModel):
    rule_results: list[_RawRuleResult] = Field(default_factory=list)


class RuleEngine:
    def __init__(
        self,
        *,
        template: PromptTemplate,
        model: str,
        agent_name: str = "flydesk-idp-rule-engine",
    ) -> None:
        self._template = template
        self._model = model
        self._agent_name = agent_name

    async def evaluate(
        self,
        rules: list[RuleSpec],
        *,
        docs: list[DocSpec],
        extracted_by_doc: dict[str, list[ExtractedFieldGroup]],
        visual_by_doc: dict[str, list[VisualValidationOutcome]],
        intention: str,
        model: str | None = None,
    ) -> list[RuleResult]:
        if not rules:
            return []

        rule_by_id: dict[str, RuleSpec] = {r.id: r for r in rules}
        # Graph nodes are rule ids (strings -- hashable). The DAG uses parent
        # rule references; field/validator parents are context, not edges.
        sorter: TopologicalSorter[str] = self._build_dag(rules, rule_by_id)
        try:
            sorter.prepare()
        except CycleError as exc:
            raise ValueError(f"Rule graph contains a cycle: {exc}") from exc

        results: list[RuleResult] = []
        # System instructions don't depend on the level -- precompile once.
        rendered_system = self._template.render(
            active_rules_json="[]",
            rules_context_json="[]",
            documents_context_json="[]",
            previous_results_context_json="[]",
            intention=intention,
        ).system
        agent: FireflyAgent[Any, _RuleEngineOutput] = FireflyAgent(
            name=self._agent_name,
            model=model or self._model,
            instructions=rendered_system,
            output_type=_RuleEngineOutput,
            description="LLM business rule evaluator",
            tags=["idp", "rules"],
            auto_register=False,
        )

        level_index = 0
        while sorter.is_active():
            ready_ids = list(sorter.get_ready())
            if not ready_ids:
                break  # safety net -- shouldn't happen for an acyclic graph
            active_rules: list[RuleSpec] = [rule_by_id[rid] for rid in ready_ids if rid in rule_by_id]
            level_index += 1
            logger.debug(
                "Evaluating rule level %d (%d rule(s))", level_index, len(active_rules)
            )

            prompt = self._template.render(
                active_rules_json=json.dumps(
                    [r.model_dump(mode="json") for r in active_rules], indent=2, ensure_ascii=False
                ),
                rules_context_json=json.dumps(
                    self._build_rules_context(active_rules), indent=2, ensure_ascii=False
                ),
                documents_context_json=json.dumps(
                    self._build_documents_context(
                        active_rules, docs, extracted_by_doc, visual_by_doc
                    ),
                    indent=2,
                    ensure_ascii=False,
                ),
                previous_results_context_json=json.dumps(
                    [
                        r.model_dump(mode="json")
                        for r in results
                        if self._used_by_any(r.rule_id, active_rules)
                    ],
                    indent=2,
                    ensure_ascii=False,
                ),
                intention=intention,
            )
            run_result = await agent.run(prompt.user)
            for raw in run_result.output.rule_results:
                results.append(
                    RuleResult(
                        rule_id=raw.rule_id,
                        predicate=raw.predicate
                        or (rule_by_id[raw.rule_id].predicate if raw.rule_id in rule_by_id else ""),
                        output=raw.output,
                        summary=raw.summary,
                        notes=list(raw.notes),
                        human_revision=raw.human_revision,
                    )
                )
            sorter.done(*ready_ids)
        return results

    # -------------------------------------------------------- DAG / context

    def _build_dag(
        self, rules: list[RuleSpec], rule_by_id: dict[str, RuleSpec]
    ) -> TopologicalSorter[str]:
        sorter: TopologicalSorter[str] = TopologicalSorter()
        for rule in rules:
            parents: list[str] = []
            for parent in rule.parents:
                if isinstance(parent, RuleRuleParent):
                    if parent.ruleId in rule_by_id:
                        parents.append(parent.ruleId)
                    else:
                        logger.warning(
                            "Rule %r references unknown parent rule %r", rule.id, parent.ruleId
                        )
            sorter.add(rule.id, *parents)
        return sorter

    def _build_rules_context(self, rules: Iterable[RuleSpec]) -> list[dict[str, Any]]:
        return [r.model_dump(mode="json") for r in rules]

    def _build_documents_context(
        self,
        active_rules: list[RuleSpec],
        docs: list[DocSpec],
        extracted_by_doc: dict[str, list[ExtractedFieldGroup]],
        visual_by_doc: dict[str, list[VisualValidationOutcome]],
    ) -> list[dict[str, Any]]:
        # Collect which fields/validators each rule touches per doc type.
        deps: dict[str, dict[str, set[str]]] = {}
        for rule in active_rules:
            for parent in rule.parents:
                if isinstance(parent, RuleFieldParent):
                    deps.setdefault(parent.documentType, {"fields": set(), "validators": set()})["fields"].update(parent.fieldNames)
                elif isinstance(parent, RuleValidatorParent):
                    deps.setdefault(parent.documentType, {"fields": set(), "validators": set()})["validators"].add(parent.validatorName)
        # Walk every required dep and emit a row per match.
        rows: list[dict[str, Any]] = []
        spec_by_type = {d.docType.documentType: d for d in docs}
        for doc_type, want in deps.items():
            spec = spec_by_type.get(doc_type)
            if spec is None:
                logger.warning("Rule references unknown documentType %r", doc_type)
                continue
            for group in extracted_by_doc.get(doc_type, []):
                for field in group.fieldGroupFields:
                    if field.fieldName in want["fields"]:
                        rows.append(
                            {
                                "documentType": doc_type,
                                "fieldGroupName": group.fieldGroupName,
                                "fieldName": field.fieldName,
                                "fieldValueFound": _serialise_field_value(field),
                                "field_validation": field.field_validation.model_dump(mode="json"),
                                "judge": field.judge.model_dump(mode="json"),
                            }
                        )
            for validator in visual_by_doc.get(doc_type, []):
                if validator.name in want["validators"]:
                    rows.append(
                        {
                            "documentType": doc_type,
                            "validatorName": validator.name,
                            "validator_passed": validator.passed,
                            "validator_confidence": validator.confidence,
                            "validator_notes": validator.notes,
                        }
                    )
        return rows

    def _used_by_any(self, parent_rule_id: str, active_rules: Iterable[RuleSpec]) -> bool:
        for rule in active_rules:
            for parent in rule.parents:
                if isinstance(parent, RuleRuleParent) and parent.ruleId == parent_rule_id:
                    return True
        return False


def _serialise_field_value(field: ExtractedField) -> Any:
    if isinstance(field.fieldValueFound, list):
        return [
            {
                "rowName": row.fieldName,
                "row": [
                    {"fieldName": sub.fieldName, "value": sub.fieldValueFound}
                    for sub in row.fieldValueFound
                    if isinstance(sub, ExtractedField)
                ],
            }
            for row in field.fieldValueFound
            if isinstance(row, ExtractedField)
        ]
    return field.fieldValueFound
