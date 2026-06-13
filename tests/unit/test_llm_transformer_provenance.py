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

"""Provenance threading, anti-fabrication, and invariant guard for LlmTransformer.

These exercise the serialization boundary that used to strip every provenance
signal before the transform LLM saw it -- the root cause of consolidations that
hallucinated a member (e.g. an administrator promoted into an ownership table)
and broke a parts-of-whole sum.
"""

from __future__ import annotations

from flydocs.core.services.transformations.llm_transformer import (
    _enforce_invariant,
    _rebuild_rows,
    _serialise_row,
)
from flydocs.interfaces.dtos.field import ExtractedField, JudgeOutcome
from flydocs.interfaces.dtos.transformation import PartsOfWholeInvariant


def _row(nombre: str, pct: float, *, conf: float = 0.9, evidence: str | None = None,
         pages: tuple[int, ...] = (1,)) -> ExtractedField:
    subs = [
        ExtractedField(
            name="nombre",
            value=nombre,
            confidence=conf,
            judge=JudgeOutcome(evidence=evidence) if evidence else JudgeOutcome(),
        ),
        ExtractedField(name="porcentaje", value=pct, confidence=conf),
    ]
    return ExtractedField(name="row", value=subs, pages=list(pages), confidence=conf)


# --------------------------------------------------------------------------- #
# _serialise_row                                                              #
# --------------------------------------------------------------------------- #


def test_serialise_row_carries_row_id_and_provenance() -> None:
    row = _row("SIGNATURE", 30.0, conf=0.9, evidence="300.000 participaciones", pages=(3,))
    out = _serialise_row(row, 0, include_provenance=True)

    assert out["nombre"] == "SIGNATURE"
    assert out["porcentaje"] == 30.0
    assert out["_row_id"] == "r1"
    prov = out["_provenance"]
    assert prov["pages"] == [3]
    assert prov["confidence"] == 0.9
    assert prov["fields"]["nombre"]["evidence"] == "300.000 participaciones"
    assert prov["fields"]["porcentaje"]["confidence"] == 0.9


def test_serialise_row_lean_when_provenance_disabled() -> None:
    out = _serialise_row(_row("X", 10.0), 4, include_provenance=False)
    assert out["_row_id"] == "r5"
    assert "_provenance" not in out


def test_serialise_row_surfaces_source_document() -> None:
    row = _row("UNICAJA", 30.0)
    row.source = "pacto_socios.pdf"
    out = _serialise_row(row, 0, include_provenance=True)
    assert out["_provenance"]["source_document"] == "pacto_socios.pdf"


def test_consolidate_stamps_source_document_per_task() -> None:
    """Rows consolidated across documents keep their originating file."""
    from flydocs.core.services.transformations.transformation_engine import _consolidate_groups
    from flydocs.interfaces.dtos.field import ExtractedFieldGroup

    def group(name: str) -> ExtractedFieldGroup:
        return ExtractedFieldGroup(
            name="socios", fields=[ExtractedField(name="socios", value=[_row(name, 50.0)])]
        )

    consolidated = _consolidate_groups(
        [[group("A")], [group("B")]], "socios", sources=["deed_a.pdf", "deed_b.pdf"]
    )
    rows = consolidated.fields[0].value
    assert [r.source for r in rows] == ["deed_a.pdf", "deed_b.pdf"]


# --------------------------------------------------------------------------- #
# _rebuild_rows -- anti-fabrication                                           #
# --------------------------------------------------------------------------- #


def test_rebuild_flags_ungrounded_row_when_invariant_present() -> None:
    """With grounding ON (the transform declares an invariant), a row whose
    identity is in NO input row was invented -- flagged (confidence 0, unmatched)
    even when wrongly cited, but never dropped here. Content-based, so a wrong
    citation does not save it."""
    inputs = [_row("SIGNATURE CAPITAL", 30.0), _row("UNICAJA BANCO", 30.0)]
    llm_rows = [
        {"nombre": "SIGNATURE CAPITAL", "porcentaje": 30.0, "_source_rows": ["r1"]},
        {"nombre": "UNICAJA BANCO", "porcentaje": 30.0, "_source_rows": ["r2"]},
        {"nombre": "NORTENA PATRIMONIAL", "porcentaje": 40.0, "_source_rows": ["r1"]},  # wrong cite
    ]
    out = _rebuild_rows(llm_rows, inputs, flag_ungrounded=True)
    names = [next(s.value for s in r.value if s.name == "nombre") for r in out]
    assert names == ["SIGNATURE CAPITAL", "UNICAJA BANCO", "NORTENA PATRIMONIAL"]  # nothing dropped
    by_name = {n: r for n, r in zip(names, out)}
    assert by_name["NORTENA PATRIMONIAL"].confidence == 0.0
    assert by_name["NORTENA PATRIMONIAL"].notes == "unmatched to source"
    assert by_name["SIGNATURE CAPITAL"].notes != "unmatched to source"
    assert all(s.name != "_source_rows" for r in out for s in r.value)  # reserved keys stripped


def test_rebuild_does_not_flag_rewritten_rows_without_invariant() -> None:
    """DECOUPLING: a value-REWRITING transform (no invariant) must NOT have its
    legitimately-rewritten rows flagged -- grounding is opt-in via flag_ungrounded
    (default False), so a translate/normalize transform that changes every string
    is never penalised."""
    inputs = [_row("BANCO SANTANDER", 50.0), _row("CAJA MADRID", 50.0)]
    llm_rows = [  # names rewritten to English -> share no token with input
        {"nombre": "SANTANDER BANK", "porcentaje": 50.0},
        {"nombre": "MADRID SAVINGS", "porcentaje": 50.0},
    ]
    out = _rebuild_rows(llm_rows, inputs)  # flag_ungrounded defaults False
    assert all(r.notes != "unmatched to source" for r in out)
    assert all(r.confidence == 0.9 for r in out)  # template confidence preserved, not zeroed


def test_rebuild_keeps_all_when_protocol_ignored() -> None:
    """No citations and no invariant -> template provenance, nothing dropped/flagged."""
    inputs = [_row("ALPHA", 50.0), _row("BETA", 50.0)]
    llm_rows = [{"nombre": "ALPHA", "porcentaje": 50.0}, {"nombre": "BETA", "porcentaje": 50.0}]
    out = _rebuild_rows(llm_rows, inputs)
    assert len(out) == 2
    assert all(r.notes != "unmatched to source" for r in out)


def test_rebuild_computes_provenance_from_contributors() -> None:
    inputs = [_row("ALPHA CORP", 30.0, conf=0.8, pages=(2,)), _row("BETA CORP", 30.0, conf=0.6, pages=(5,))]
    llm_rows = [{"nombre": "ALPHA CORP", "porcentaje": 60.0, "_source_rows": ["r1", "r2"]}]
    out = _rebuild_rows(llm_rows, inputs)
    assert out[0].pages == [2, 5]
    assert out[0].confidence == 0.7  # mean(0.8, 0.6) -- citation honoured


# --------------------------------------------------------------------------- #
# _enforce_invariant                                                          #
# --------------------------------------------------------------------------- #


def test_invariant_repairs_oversum_by_dropping_least_trusted() -> None:
    rows = [
        _row("SIGNATURE", 30.0, conf=0.95),
        _row("ANDRES", 10.0, conf=0.95),
        _row("UNICAJA", 30.0, conf=0.95),
        _row("CASER", 30.0, conf=0.95),
        _row("NORTEÑA", 10.0, conf=0.20),  # lowest trust -> dropped
    ]
    inv = PartsOfWholeInvariant(share_field="porcentaje", total=100.0)
    out = _enforce_invariant(rows, inv, "tid12345")
    names = [next(s.value for s in r.value if s.name == "nombre") for r in out]
    assert "NORTEÑA" not in names
    assert sum(next(s.value for s in r.value if s.name == "porcentaje") for r in out) == 100.0


def test_invariant_warn_mode_leaves_rows_untouched() -> None:
    rows = [_row("A", 70.0), _row("B", 50.0)]
    inv = PartsOfWholeInvariant(share_field="porcentaje", total=100.0, on_violation="warn")
    out = _enforce_invariant(rows, inv, "tid")
    assert len(out) == 2


def test_invariant_undersum_never_altered() -> None:
    rows = [_row("A", 30.0), _row("B", 30.0)]
    inv = PartsOfWholeInvariant(share_field="porcentaje", total=100.0)
    out = _enforce_invariant(rows, inv, "tid")
    assert len(out) == 2
