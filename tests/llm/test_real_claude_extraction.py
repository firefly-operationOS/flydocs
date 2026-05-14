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
- pure-Python field validation including :class:`StandardValidator`
  checks (NIF / NIE),
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

from flydesk_idp.interfaces.dtos.doc import DocSpec, DocType, ValidatorsSpec, VisualValidatorSpec
from flydesk_idp.interfaces.dtos.extract import (
    DocumentInput,
    ExtractionOptions,
    ExtractionRequest,
    StageToggles,
)
from flydesk_idp.interfaces.dtos.field import FieldGroup, FieldSpec
from flydesk_idp.interfaces.dtos.rule import RuleFieldParent, RuleOutputSpec, RuleSpec
from flydesk_idp.interfaces.dtos.standard_validator import StandardValidatorSpec
from flydesk_idp.interfaces.enums.field_type import FieldType
from flydesk_idp.interfaces.enums.standard_validator import StandardValidatorType

PDF_PATH = Path.home() / "Downloads" / "escritura_poderes_2025.pdf"
MODEL = os.environ.get("FLYDESK_IDP_TEST_MODEL", "anthropic:claude-opus-4-7")


# ===========================================================================
# Test schema -- what we want extracted from the Spanish notarial deed.
# ===========================================================================

_DOC_TYPE = "escritura_poderes"

_FIELD_SPECS = [
    FieldSpec(
        fieldName="numero_protocolo",
        fieldDescription="Número de protocolo notarial.",
        fieldType=FieldType.STRING,
    ),
    FieldSpec(
        fieldName="fecha",
        fieldDescription="Fecha del otorgamiento en formato ISO YYYY-MM-DD.",
        fieldType=FieldType.STRING,
        standard_validators=[StandardValidatorSpec(type=StandardValidatorType.DATE)],
    ),
    FieldSpec(
        fieldName="notario",
        fieldDescription="Nombre completo del notario que autoriza.",
        fieldType=FieldType.STRING,
    ),
    FieldSpec(
        fieldName="otorgante_nombre",
        fieldDescription="Nombre completo del otorgante (poderdante).",
        fieldType=FieldType.STRING,
    ),
    FieldSpec(
        fieldName="otorgante_dni_nie",
        fieldDescription="DNI o NIE del otorgante (8 dígitos + letra, o letra + 7 dígitos + letra).",
        fieldType=FieldType.STRING,
        standard_validators=[
            StandardValidatorSpec(type=StandardValidatorType.NIF, severity="warning"),
            StandardValidatorSpec(type=StandardValidatorType.NIE, severity="warning"),
        ],
    ),
    FieldSpec(
        fieldName="apoderado_nombre",
        fieldDescription="Nombre completo del apoderado.",
        fieldType=FieldType.STRING,
    ),
    FieldSpec(
        fieldName="apoderado_dni_nie",
        fieldDescription="DNI o NIE del apoderado.",
        fieldType=FieldType.STRING,
        standard_validators=[
            StandardValidatorSpec(type=StandardValidatorType.NIF, severity="warning"),
            StandardValidatorSpec(type=StandardValidatorType.NIE, severity="warning"),
        ],
    ),
]

_VISUAL_VALIDATORS = [
    VisualValidatorSpec(
        name="firma_notario",
        description="The notary's handwritten signature is present.",
    ),
    VisualValidatorSpec(
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
                parentType="field",
                documentType=_DOC_TYPE,
                fieldNames=[
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
                parentType="field",
                documentType=_DOC_TYPE,
                fieldNames=["otorgante_nombre", "apoderado_nombre"],
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
                parentType="field",
                documentType=_DOC_TYPE,
                fieldNames=["fecha"],
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
    out.append(f"  flydesk-idp -- real Claude run ({result.model})")
    out.append("=" * 70)
    out.append("")
    out.append(f"document      : {result.document.filename} ({result.document.media_type})")
    out.append(f"pages         : {result.document.page_count}")
    out.append(f"bytes         : {result.document.bytes:,}")
    out.append(f"latency_ms    : {result.latency_ms:,}")
    out.append(f"request_id    : {result.request_id}")
    out.append("")

    for doc in result.documents:
        out.append("-" * 70)
        out.append(f"document_type : {doc.document_type}")
        out.append(f"pages         : {doc.pages}")
        out.append(f"confidence    : {doc.confidence:.2f}")
        out.append("")
        out.append(f"{'FIELD':25s} {'VALUE':40s} {'CONF':>5s} {'PAGE':>4s} {'JUDGE':10s} {'VALID':6s}")
        for group in doc.fields:
            out.append(f"  [group] {group.fieldGroupName}")
            for f in group.fieldGroupFields:
                value = f.fieldValueFound if f.fieldValueFound is not None else "—"
                value_s = str(value)[:38]
                pages_s = ",".join(str(p) for p in f.pagesFound) or "—"
                judge_s = f.judge.status.value if f.judge else "—"
                fv = f.field_validation
                valid_s = "OK" if fv.valid else "BAD"
                out.append(
                    f"  {f.fieldName:25s} {value_s:40s} {f.confidence:.2f}  "
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
                out.append(f"  - {v.name:30s} {badge:5s} conf={v.confidence:.2f} -- {v.notes[:60]}")
            out.append("")

    if result.rule_results:
        out.append("-" * 70)
        out.append("BUSINESS RULES")
        for r in result.rule_results:
            out.append(f"  - {r.rule_id:25s} -> {r.output:10s} {r.summary[:80]}")
        out.append("")

    if result.pipeline_errors:
        out.append("PIPELINE ERRORS:")
        out.append(json.dumps(result.pipeline_errors, indent=2))
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
    doc_spec = DocSpec(
        docType=DocType(
            documentType=_DOC_TYPE,
            description="Escritura notarial de poderes (Spanish notarial power of attorney)",
            country="ES",
        ),
        fieldGroups=[
            FieldGroup(
                fieldGroupName="otorgamiento",
                fieldGroupDesc="Datos del otorgamiento",
                fieldGroupFields=_FIELD_SPECS,
            )
        ],
        validators=ValidatorsSpec(visual=_VISUAL_VALIDATORS),
    )

    request = ExtractionRequest(
        intention=(
            "Audit a Spanish notarial power of attorney for KYC purposes. "
            "Extract the canonical fields, verify the notary's signature is "
            "present, and evaluate whether the document is complete and recent."
        ),
        document=DocumentInput(
            filename=PDF_PATH.name,
            content_base64=base64.b64encode(pdf_bytes).decode("ascii"),
            content_type="application/pdf",
        ),
        docs=[doc_spec],
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
    assert result.document.media_type == "application/pdf"
    assert result.document.page_count >= 1
    assert len(result.documents) == 1
    doc = result.documents[0]
    assert doc.document_type == _DOC_TYPE
    assert doc.missing is False

    fields = doc.fields[0].fieldGroupFields
    expected_names = {fs.fieldName for fs in _FIELD_SPECS}
    assert {f.fieldName for f in fields} == expected_names

    located = [f for f in fields if f.fieldValueFound is not None]
    assert len(located) >= 4, "Expected most fields located"

    # Every located field must carry a non-empty bbox AND a page.
    for f in located:
        assert f.pagesFound, f"Located field {f.fieldName!r} has no pages"
        assert all(p >= 1 for p in f.pagesFound)
        assert f.bbox.xmax > f.bbox.xmin and f.bbox.ymax > f.bbox.ymin, (
            f"Located field {f.fieldName!r} has degenerate bbox"
        )

    # Judge must have stamped a verdict on every located field.
    for f in located:
        assert f.judge.status.value in {"PASS", "FAIL", "UNCERTAIN"}

    # Visual authenticity must have one outcome per requested validator.
    assert len(doc.authenticity.visual) == len(_VISUAL_VALIDATORS)
    by_name = {v.name: v for v in doc.authenticity.visual}
    assert set(by_name.keys()) == {v.name for v in _VISUAL_VALIDATORS}

    # Rule engine must have evaluated every rule.
    assert len(result.rule_results) == len(_RULES)
    by_rule = {r.rule_id: r for r in result.rule_results}
    assert set(by_rule.keys()) == {r.id for r in _RULES}
    for r in result.rule_results:
        assert r.output in {"true", "false", "unknown", ""} or r.output.lower() in {"true", "false"}


async def _run_via_di(request: ExtractionRequest):
    """Boot pyfly, resolve the orchestrator from the container, run."""
    from pyfly.core import PyFlyApplication

    from flydesk_idp.app import FlydeskIDPApplication
    from flydesk_idp.core.services.pipeline import PipelineOrchestrator

    pyfly_app = PyFlyApplication(FlydeskIDPApplication)
    await pyfly_app.startup()
    try:
        orchestrator: PipelineOrchestrator = pyfly_app.context.container.resolve(PipelineOrchestrator)
        return await orchestrator.execute(request)
    finally:
        await pyfly_app.shutdown()
