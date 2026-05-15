# Copyright 2026 Firefly Software Solutions Inc
"""``EntityResolutionTransformer`` -- declarative dedup of array rows.

Coverage:

1. DNI-first merge collapses two rows whose DNIs match modulo
   formatting differences.
2. Name-variant merge bridges accent-folded + token-subset names
   ("Andrés Contreras" + "Andres Contreras Guillen").
3. ``min_shared_tokens`` enforcement prevents merging on a single
   shared first name.
4. ``output_group`` leaves the original intact and adds a new group.
5. Target group missing -> no-op (returns ``None``, groups untouched).
"""

from __future__ import annotations

from flydesk_idp.core.services.transformations.entity_resolution import (
    EntityResolutionTransformer,
)
from flydesk_idp.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup
from flydesk_idp.interfaces.dtos.transformation import EntityResolutionTransformation


def _row(values: dict[str, str]) -> ExtractedField:
    """Build one persona row from a flat dict of sub-field values."""
    return ExtractedField(
        fieldName="row",
        fieldValueFound=[ExtractedField(fieldName=k, fieldValueFound=v) for k, v in values.items()],
    )


def _personas_group(rows: list[ExtractedField]) -> ExtractedFieldGroup:
    return ExtractedFieldGroup(
        fieldGroupName="personas",
        fieldGroupFields=[ExtractedField(fieldName="personas", fieldValueFound=rows)],
    )


def _row_names(group: ExtractedFieldGroup) -> list[str]:
    """Pull the ``nombre`` value out of each row for assertions."""
    out: list[str] = []
    for f in group.fieldGroupFields:
        if not isinstance(f.fieldValueFound, list):
            continue
        for row in f.fieldValueFound:
            for sub in row.fieldValueFound or []:
                if sub.fieldName == "nombre":
                    out.append(sub.fieldValueFound)  # type: ignore[arg-type]
                    break
    return out


# ----------------------------------------------------------------- tests


def test_dni_match_merges_rows() -> None:
    """Same person, two formats of the same DNI -> one canonical row."""
    rows = [
        _row({"nombre": "Joaquín Sevilla", "dni": "07549861L"}),
        _row({"nombre": "Joaquín Sevilla Rodríguez", "dni": "07.549.861-L"}),
    ]
    groups = [_personas_group(rows)]
    t = EntityResolutionTransformation(
        target_group="personas",
        match_by=["dni", "nombre"],
    )
    EntityResolutionTransformer().apply(t, groups)

    names = _row_names(groups[0])
    assert len(names) == 1
    # Canonical name picks the longest variant.
    assert names[0] == "Joaquín Sevilla Rodríguez"


def test_name_variant_merges_accent_and_subset() -> None:
    """No DNI on either row; accent fold + token subset bridges them."""
    rows = [
        _row({"nombre": "Andrés Contreras", "dni": ""}),
        _row({"nombre": "Andres Contreras Guillen", "dni": ""}),
    ]
    groups = [_personas_group(rows)]
    t = EntityResolutionTransformation(
        target_group="personas",
        match_by=["dni", "nombre"],
        min_shared_tokens=2,
    )
    EntityResolutionTransformer().apply(t, groups)

    names = _row_names(groups[0])
    assert names == ["Andres Contreras Guillen"]


def test_single_token_does_not_merge_strangers() -> None:
    """Two unrelated people sharing a first name must NOT merge."""
    rows = [
        _row({"nombre": "Andrés Lopez", "dni": ""}),
        _row({"nombre": "Andrés Garcia", "dni": ""}),
    ]
    groups = [_personas_group(rows)]
    t = EntityResolutionTransformation(
        target_group="personas",
        match_by=["dni", "nombre"],
        min_shared_tokens=2,
    )
    EntityResolutionTransformer().apply(t, groups)

    names = _row_names(groups[0])
    assert sorted(names) == ["Andrés Garcia", "Andrés Lopez"]


def test_output_group_preserves_original() -> None:
    """``output_group`` -> two groups in the result, original untouched."""
    rows = [
        _row({"nombre": "Andrés Contreras", "dni": ""}),
        _row({"nombre": "Andres Contreras Guillen", "dni": ""}),
    ]
    groups = [_personas_group(rows)]
    t = EntityResolutionTransformation(
        target_group="personas",
        output_group="personas_normalized",
        match_by=["dni", "nombre"],
    )
    EntityResolutionTransformer().apply(t, groups)

    assert len(groups) == 2
    assert {g.fieldGroupName for g in groups} == {"personas", "personas_normalized"}
    assert len(_row_names(groups[0])) == 2  # original untouched
    assert len(_row_names(groups[1])) == 1  # dedupe applied


def test_missing_target_group_is_noop() -> None:
    """Unknown ``target_group`` -> None + no mutation."""
    groups = [_personas_group([_row({"nombre": "x", "dni": ""})])]
    t = EntityResolutionTransformation(
        target_group="does_not_exist",
        match_by=["dni", "nombre"],
    )
    result = EntityResolutionTransformer().apply(t, groups)
    assert result is None
    assert len(groups) == 1
    assert _row_names(groups[0]) == ["x"]
