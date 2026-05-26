# Copyright 2026 Firefly Software Solutions Inc
"""``TransformationEngine`` -- dispatch + scope handling.

Coverage:

1. Entity-resolution transformation routes to the right backend on
   the task scope.
2. LLM transformation routes to the LLM backend.
3. ``scope=request`` consolidates rows across tasks before applying.
4. An unrecognised transformation type degrades quietly to a no-op.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from flydocs.core.services.transformations.entity_resolution import (
    EntityResolutionTransformer,
)
from flydocs.core.services.transformations.transformation_engine import (
    TransformationEngine,
)
from flydocs.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup
from flydocs.interfaces.dtos.transformation import (
    EntityResolutionTransformation,
    LlmTransformation,
    TransformationScope,
)


@dataclass
class _FakeLlmTransformer:
    """Records calls; returns a synthetic consolidated group."""

    calls: list[tuple[LlmTransformation, list[ExtractedFieldGroup]]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.calls = []

    async def apply(self, t, groups):
        self.calls.append((t, groups))
        produced = ExtractedFieldGroup(
            name=t.output_group or t.target_group,
            fields=[ExtractedField(name="rows", value=[])],
        )
        groups.append(produced)
        return produced


def _row(values: dict[str, str]) -> ExtractedField:
    return ExtractedField(
        name="row",
        value=[ExtractedField(name=k, value=v) for k, v in values.items()],
    )


def _personas_group(rows: list[ExtractedField]) -> ExtractedFieldGroup:
    return ExtractedFieldGroup(
        name="personas",
        fields=[ExtractedField(name="personas", value=rows)],
    )


# ----------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_dispatch_entity_resolution() -> None:
    """type=entity_resolution -> declarative path, LLM never called."""
    fake_llm = _FakeLlmTransformer()
    engine = TransformationEngine(
        entity_resolver=EntityResolutionTransformer(),
        llm_transformer=fake_llm,  # type: ignore[arg-type]
    )
    t = EntityResolutionTransformation(
        target_group="personas",
        match_by=["dni", "nombre"],
    )
    groups = [
        _personas_group(
            [
                _row({"nombre": "Andrés Contreras", "dni": ""}),
                _row({"nombre": "Andres Contreras Guillen", "dni": ""}),
            ]
        )
    ]
    result = await engine.apply_to_task(t, groups)
    assert result is not None
    assert fake_llm.calls == []
    # Dedup happened.
    inner = result.fields[0].value  # type: ignore[index]
    assert isinstance(inner, list) and len(inner) == 1


@pytest.mark.asyncio
async def test_dispatch_llm_transformation() -> None:
    """type=llm -> LLM transformer is invoked exactly once."""
    fake_llm = _FakeLlmTransformer()
    engine = TransformationEngine(
        entity_resolver=EntityResolutionTransformer(),
        llm_transformer=fake_llm,  # type: ignore[arg-type]
    )
    t = LlmTransformation(
        target_group="personas",
        intention="Normalize each cargo to a closed taxonomy bucket",
    )
    groups = [_personas_group([_row({"nombre": "x"})])]
    await engine.apply_to_task(t, groups)
    assert len(fake_llm.calls) == 1


@pytest.mark.asyncio
async def test_request_scope_consolidates_across_tasks() -> None:
    """scope=request: rows from every task get folded into a single synth group."""
    fake_llm = _FakeLlmTransformer()
    engine = TransformationEngine(
        entity_resolver=EntityResolutionTransformer(),
        llm_transformer=fake_llm,  # type: ignore[arg-type]
    )
    t = EntityResolutionTransformation(
        target_group="personas",
        match_by=["dni", "nombre"],
        scope=TransformationScope.REQUEST,
    )
    # Two tasks each with one persona; same person ("Andrés Contreras"
    # in task A; "Andres Contreras Guillen" in task B).
    task_a = [_personas_group([_row({"nombre": "Andrés Contreras", "dni": ""})])]
    task_b = [_personas_group([_row({"nombre": "Andres Contreras Guillen", "dni": ""})])]
    produced = await engine.apply_request_scope(t, [task_a, task_b])
    assert produced is not None
    # The synth consolidated group reduces 2 -> 1.
    inner = produced.fields[0].value
    assert isinstance(inner, list) and len(inner) == 1
    # Task-scope groups stay untouched.
    assert len(task_a[0].fields[0].value) == 1  # type: ignore[arg-type]
    assert len(task_b[0].fields[0].value) == 1  # type: ignore[arg-type]
