# Copyright 2026 Firefly Software Solutions Inc
"""Multi-transformation requests.

``ExtractionOptions.transformations`` is a ``list[Transformation]``,
so a single request can chain several transformations against the
same (or different) target groups. The engine applies them in order
which lets callers, e.g., first dedupe rows declaratively and then
ask the LLM to classify each survivor.

Coverage:

1. Two declarative transformations on the same target apply
   sequentially — the second sees the output of the first.
2. Mixing an ``entity_resolution`` followed by an ``llm`` transformation
   dispatches each to its proper backend, and the LLM is invoked
   on the post-dedup rows (not the originals).
3. An empty ``transformations`` list is a no-op even with the stage
   toggle on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from flydesk_idp.core.services.transformations.entity_resolution import (
    EntityResolutionTransformer,
)
from flydesk_idp.core.services.transformations.transformation_engine import (
    TransformationEngine,
)
from flydesk_idp.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup
from flydesk_idp.interfaces.dtos.transformation import (
    EntityResolutionTransformation,
    LlmTransformation,
)


@dataclass
class _FakeLlmTransformer:
    """Captures the rows it sees so tests can assert ordering."""

    calls: list[list[str]] = field(default_factory=list)

    async def apply(self, t, groups):
        target = next((g for g in groups if g.fieldGroupName == t.target_group), None)
        names: list[str] = []
        if target is not None:
            arr = next((f for f in target.fieldGroupFields if isinstance(f.fieldValueFound, list)), None)
            if arr is not None:
                for row in arr.fieldValueFound or []:
                    if isinstance(row, ExtractedField):
                        for sub in row.fieldValueFound or []:
                            if isinstance(sub, ExtractedField) and sub.fieldName == "nombre":
                                names.append(str(sub.fieldValueFound))
                                break
        self.calls.append(names)
        # Echo the input shape back unchanged; mutate the target group.
        return target


def _row(**values: str) -> ExtractedField:
    return ExtractedField(
        name="row",
        value=[ExtractedField(name=k, value=v) for k, v in values.items()],
    )


def _personas(rows: list[ExtractedField]) -> ExtractedFieldGroup:
    return ExtractedFieldGroup(
        fieldGroupName="personas",
        fieldGroupFields=[ExtractedField(name="personas", value=rows)],
    )


def _row_names(group: ExtractedFieldGroup) -> list[str]:
    out: list[str] = []
    for f in group.fieldGroupFields:
        if not isinstance(f.fieldValueFound, list):
            continue
        for row in f.fieldValueFound:
            for sub in row.fieldValueFound or []:
                if sub.fieldName == "nombre":
                    out.append(str(sub.fieldValueFound))
                    break
    return out


# ----------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_two_declarative_transformations_chain() -> None:
    """Two entity_resolution rounds on the same target apply in order."""
    fake_llm = _FakeLlmTransformer()
    engine = TransformationEngine(
        entity_resolver=EntityResolutionTransformer(),
        llm_transformer=fake_llm,  # type: ignore[arg-type]
    )

    transformations = [
        EntityResolutionTransformation(target_group="personas", match_by=["dni", "nombre"]),
        EntityResolutionTransformation(target_group="personas", match_by=["dni", "nombre"]),
    ]
    groups = [
        _personas(
            [
                _row(nombre="Andrés Contreras", dni=""),
                _row(nombre="Andres Contreras Guillen", dni=""),
            ]
        )
    ]
    for t in transformations:
        await engine.apply_to_task(t, groups)

    # Both dedupe to one row; the second pass is a no-op (already one row).
    assert _row_names(groups[0]) == ["Andres Contreras Guillen"]


@pytest.mark.asyncio
async def test_entity_then_llm_sees_deduped_rows() -> None:
    """Declarative dedup runs first; LLM sees the deduped row set."""
    fake_llm = _FakeLlmTransformer()
    engine = TransformationEngine(
        entity_resolver=EntityResolutionTransformer(),
        llm_transformer=fake_llm,  # type: ignore[arg-type]
    )

    transformations = [
        EntityResolutionTransformation(target_group="personas", match_by=["dni", "nombre"]),
        LlmTransformation(
            target_group="personas",
            intention="Classify each cargo into a closed taxonomy.",
        ),
    ]
    groups = [
        _personas(
            [
                _row(nombre="Andrés Contreras", dni=""),
                _row(nombre="Andres Contreras Guillen", dni=""),
            ]
        )
    ]
    for t in transformations:
        await engine.apply_to_task(t, groups)

    # The LLM was called exactly once.
    assert len(fake_llm.calls) == 1
    # And it saw the deduped row, not the originals.
    assert fake_llm.calls[0] == ["Andres Contreras Guillen"]


@pytest.mark.asyncio
async def test_empty_list_is_noop() -> None:
    """An empty list of transformations leaves groups untouched."""
    fake_llm = _FakeLlmTransformer()
    engine = TransformationEngine(
        entity_resolver=EntityResolutionTransformer(),
        llm_transformer=fake_llm,  # type: ignore[arg-type]
    )
    groups = [_personas([_row(nombre="Marta")])]
    transformations: list = []
    for t in transformations:
        await engine.apply_to_task(t, groups)
    assert _row_names(groups[0]) == ["Marta"]
    assert fake_llm.calls == []
