# Copyright 2026 Firefly Software Solutions Inc
"""End-to-end smoke test against a real Anthropic Claude model.

Opt-in: only runs when ``ANTHROPIC_API_KEY`` is set AND the sample PDF
is present on disk. Boots the actual :class:`PyFlyApplication`, resolves
the :class:`PipelineOrchestrator` from the pyfly DI container, and
runs an extraction request end-to-end -- no manual wiring. This is the
same wiring path the production REST controller uses, so a green run
here means the whole DI graph (settings, prompt catalog, every stage,
orchestrator) is healthy.

Exercises:

- multimodal extraction with bounding boxes,
- pure-Python field validation including built-in validator checks
  (NIF / NIE),
- visual-authenticity LLM check,
- LLM judge re-evaluation,
- the business :class:`RuleEngine` with cross-field predicates.

Fixture PDF: :file:`~/Downloads/escritura_poderes_2025.pdf` (a Spanish
notarial power-of-attorney deed). The test prints the fully-formed
result so a human can eyeball it.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path

import pytest

from flydocs.interfaces.dtos.document_type import DocumentTypeSpec, VisualCheck
from flydocs.interfaces.dtos.extract import (
    ExtractionOptions,
    ExtractionRequest,
    FileInput,
    StageToggles,
)
from flydocs.interfaces.dtos.field import Field, FieldGroup
from flydocs.interfaces.dtos.rule import RuleFieldParent, RuleOutputSpec, RuleSpec
from flydocs.interfaces.dtos.validator import ValidatorSpec
from flydocs.interfaces.enums.field_type import FieldType
from flydocs.interfaces.enums.validator import ValidatorType

PDF_PATH = Path.home() / "Downloads" / "escritura_poderes_2025.pdf"
MODEL = os.environ.get("FLYDOCS_TEST_MODEL", "anthropic:claude-opus-4-7")


# ===========================================================================
# Test schema -- what we want extracted from the Spanish notarial deed.
# ===========================================================================

_DOC_TYPE = "escritura_poderes"

_FIELDS = [
    Field(
        name="numero_protocolo",
        description="Número de protocolo notarial.",
        type=FieldType.STRING,
    ),
    Field(
        name="fecha",
        description="Fecha del otorgamiento en formato ISO YYYY-MM-DD.",
        type=FieldType.STRING,
        validators=[ValidatorSpec(name=ValidatorType.DATE)],
    ),
    Field(
        name="notario",
        description="Nombre completo del notario que autoriza.",
        type=FieldType.STRING,
    ),
    Field(
        name="otorgante_nombre",
        description="Nombre completo del otorgante (poderdante).",
        type=FieldType.STRING,
    ),
    Field(
        name="otorgante_dni_nie",
        description="DNI o NIE del otorgante (8 dígitos + letra, o letra + 7 dígitos + letra).",
        type=FieldType.STRING,
        validators=[
            ValidatorSpec(name=ValidatorType.NIF, severity="warning"),
            ValidatorSpec(name=ValidatorType.NIE, severity="warning"),
        ],
    ),
    Field(
        name="apoderado_nombre",
        description="Nombre completo del apoderado.",
        type=FieldType.STRING,
    ),
    Field(
        name="apoderado_dni_nie",
        description="DNI o NIE del apoderado.",
        type=FieldType.STRING,
        validators=[
            ValidatorSpec(name=ValidatorType.NIF, severity="warning"),
            ValidatorSpec(name=ValidatorType.NIE, severity="warning"),
        ],
    ),
]

_VISUAL_CHECKS = [
    VisualCheck(
        name="firma_notario",
        description="The notary's handwritten signature is present.",
    ),
    VisualCheck(
        name="sello_notarial",
        description="The notary's official stamp / seal is present.",
    ),
]

_RULES = [
    RuleSpec(
        id="kyc_complete",
        predicate=(
            "Both otorgante_nombre and apoderado_nombre are populated, "
            "and otorgante_dni_nie and apoderado_dni_nie are populated, "
            "and fecha is populated."
        ),
        parents=[
            RuleFieldParent(
                kind="field",
                document_type=_DOC_TYPE,
                fields=[
                    "otorgante_nombre",
                    "apoderado_nombre",
                    "otorgante_dni_nie",
                    "apoderado_dni_nie",
                    "fecha",
                ],
            ),
        ],
        output=RuleOutputSpec(type="boolean", valid_outputs=["true", "false"]),
    ),
    RuleSpec(
        id="parties_distinct",
        predicate=(
            "The otorgante_nombre and apoderado_nombre refer to different "
            "individuals (case- and accent-insensitive)."
        ),
        parents=[
            RuleFieldParent(
                kind="field",
                document_type=_DOC_TYPE,
                fields=["otorgante_nombre", "apoderado_nombre"],
            ),
        ],
        output=RuleOutputSpec(type="boolean", valid_outputs=["true", "false"]),
    ),
    RuleSpec(
        id="recent_document",
        predicate=(
            "The ``fecha`` is on or after 2020-01-01. Return ``true`` if "
            "the date is recent enough, ``false`` if older."
        ),
        parents=[
            RuleFieldParent(
                kind="field",
                document_type=_DOC_TYPE,
                fields=["fecha"],
            ),
        ],
        output=RuleOutputSpec(type="boolean", valid_outputs=["true", "false"]),
    ),
]


# ===========================================================================
# Pretty-printer for the result (zero-dep)
# ===========================================================================


def _render(result, request) -> str:
    out: list[str] = []
    out.append("=" * 70)
    out.append(f"  flydocs -- real Claude run ({result.pipeline.model})")
    out.append("=" * 70)
    out.append("")
    primary = result.files[0]
    out.append(f"document      : {primary.filename} ({primary.media_type})")
    out.append(f"pages         : {primary.page_count}")
    out.append(f"bytes         : {primary.bytes:,}")
    out.append(f"latency_ms    : {result.pipeline.latency_ms:,}")
    out.append(f"id            : {result.id}")
    out.append("")

    for doc in result.documents:
        out.append("-" * 70)
        out.append(f"document_type : {doc.type}")
        out.append(f"pages         : {doc.pages}")
        out.append(f"confidence    : {doc.confidence:.2f}")
        out.append("")
        out.append(f"{'FIELD':25s} {'VALUE':40s} {'CONF':>5s} {'PAGE':>4s} {'JUDGE':10s} {'VALID':6s}")
        for group in doc.field_groups:
            out.append(f"  [group] {group.name}")
            for f in group.fields:
                value = f.value if f.value is not None else "—"
                value_s = str(value)[:38]
                pages_s = ",".join(str(p) for p in f.pages) or "—"
                judge_s = f.judge.status.value if f.judge else "—"
                fv = f.validation
                valid_s = "OK" if fv.valid else "BAD"
                out.append(
                    f"  {f.name:25s} {value_s:40s} {f.confidence:.2f}  "
                    f"{pages_s:>4s} {judge_s:10s} {valid_s:6s}"
                )
                if fv.errors:
                    for err in fv.errors:
                        out.append(f"      validation: [{err.rule.value}] {err.message}")
                if f.judge and f.judge.notes:
                    out.append(f"      judge: {f.judge.notes[:80]}")
                if f.bbox and f.bbox.xmax > f.bbox.xmin:
                    out.append(
                        f"      bbox: ({f.bbox.xmin:.2f},{f.bbox.ymin:.2f})-({f.bbox.xmax:.2f},{f.bbox.ymax:.2f})"
                    )
        out.append("")
        if doc.authenticity.visual:
            out.append("VISUAL AUTHENTICITY")
            for v in doc.authenticity.visual:
                badge = "PASS" if v.passed else "FAIL"
                out.append(f"  - {v.name:30s} {badge:5s} conf={v.confidence:.2f} -- {(v.notes or '')[:60]}")
            out.append("")

    if result.rule_results:
        out.append("-" * 70)
        out.append("BUSINESS RULES")
        for r in result.rule_results:
            summary = r.summary or ""
            out.append(f"  - {r.rule_id:25s} -> {r.output:10s} {summary[:80]}")
        out.append("")

    if result.pipeline.errors:
        out.append("PIPELINE ERRORS:")
        out.append(json.dumps([e.model_dump() for e in result.pipeline.errors], indent=2))
    out.append("=" * 70)
    return "\n".join(out)


# ===========================================================================
# Test
# ===========================================================================


@pytest.mark.llm
def test_real_claude_extraction_with_rules() -> None:
    """End-to-end: real Claude call -> extract + validate + judge + rules.

    Boots a full :class:`PyFlyApplication`, resolves the orchestrator
    from the DI container, and verifies every stage produces the
    expected shape.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set; export the env var to exercise this test.")
    if not PDF_PATH.exists():
        pytest.skip(f"Sample PDF {PDF_PATH} not found locally.")

    pdf_bytes = PDF_PATH.read_bytes()
    doc_spec = DocumentTypeSpec(
        id=_DOC_TYPE,
        description="Escritura notarial de poderes (Spanish notarial power of attorney)",
        country="ES",
        field_groups=[
            FieldGroup(
                name="otorgamiento",
                description="Datos del otorgamiento",
                fields=_FIELDS,
            )
        ],
        visual_checks=_VISUAL_CHECKS,
    )

    request = ExtractionRequest(
        intention=(
            "Audit a Spanish notarial power of attorney for KYC purposes. "
            "Extract the canonical fields, verify the notary's signature is "
            "present, and evaluate whether the document is complete and recent."
        ),
        files=[
            FileInput(
                filename=PDF_PATH.name,
                content_base64=base64.b64encode(pdf_bytes).decode("ascii"),
                content_type="application/pdf",
            )
        ],
        document_types=[doc_spec],
        rules=_RULES,
        options=ExtractionOptions(
            model=MODEL,
            language_hint="es",
            stages=StageToggles(
                splitter=False,  # single doc-type, no need to split
                field_validation=True,
                visual_authenticity=True,
                content_authenticity=False,
                judge=True,
                rule_engine=True,
            ),
        ),
    )

    result = asyncio.run(_run_via_di(request))

    # -------- pretty-print the result -----------------------------------
    print("\n" + _render(result, request))

    # -------- assertions ------------------------------------------------
    assert result.files[0].media_type == "application/pdf"
    assert result.files[0].page_count >= 1
    assert len(result.documents) == 1
    doc = result.documents[0]
    assert doc.type == _DOC_TYPE
    assert doc.missing is False

    fields = doc.field_groups[0].fields
    expected_names = {f.name for f in _FIELDS}
    assert {f.name for f in fields} == expected_names

    located = [f for f in fields if f.value is not None]
    assert len(located) >= 4, "Expected most fields located"

    # Every located field must carry a non-empty bbox AND a page.
    for f in located:
        assert f.pages, f"Located field {f.name!r} has no pages"
        assert all(p >= 1 for p in f.pages)
        assert f.bbox is not None
        assert f.bbox.xmax > f.bbox.xmin and f.bbox.ymax > f.bbox.ymin, (
            f"Located field {f.name!r} has degenerate bbox"
        )

    # Judge must have stamped a verdict on every located field.
    for f in located:
        assert f.judge.status.value in {"pass", "fail", "uncertain"}

    # Visual authenticity must have one outcome per requested check.
    assert len(doc.authenticity.visual) == len(_VISUAL_CHECKS)
    by_name = {v.name: v for v in doc.authenticity.visual}
    assert set(by_name.keys()) == {v.name for v in _VISUAL_CHECKS}

    # Rule engine must have evaluated every rule.
    assert len(result.rule_results) == len(_RULES)
    by_rule = {r.rule_id: r for r in result.rule_results}
    assert set(by_rule.keys()) == {r.id for r in _RULES}
    for r in result.rule_results:
        assert r.output in {"true", "false", "unknown", ""} or r.output.lower() in {
            "true",
            "false",
        }


async def _run_via_di(request: ExtractionRequest):
    """Boot pyfly, resolve the orchestrator from the container, run."""
    from pyfly.core import PyFlyApplication

    from flydocs.app import FlydocsApplication
    from flydocs.core.services.pipeline import PipelineOrchestrator

    pyfly_app = PyFlyApplication(FlydocsApplication)
    await pyfly_app.startup()
    try:
        orchestrator: PipelineOrchestrator = pyfly_app.context.container.resolve(PipelineOrchestrator)
        return await orchestrator.execute(request)
    finally:
        await pyfly_app.shutdown()
