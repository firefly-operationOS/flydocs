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

"""End-to-end KYB extraction smoke test against the running Docker stack.

Phases:
  1. SYNC  — POST /api/v1/extract with the smaller pacto-de-socios PDF
              and a minimal pipeline (no judge, no bbox refine, no rules).
              Validates that the v1 sync endpoint works against the real
              Anthropic LLM in under the 60s ceiling.
  2. ASYNC — POST /api/v1/extractions with BOTH PDFs (escritura +
              pacto), all schemas, KYB-relevant cross-document rules,
              judge + bbox_refine + rule_engine all on. Polls until
              terminal, then fetches /result.

Run:
  uv run python scripts/kyb_real_test.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

API = os.environ.get("FLYDOCS_API", "http://localhost:8400")

DEED_PDF = Path("/Users/ancongui/Downloads/resolicituddedocumentacin/2023.03.17_Escrit. consitiucion_DF&IS_registrada.pdf")
PACTO_PDF = Path("/Users/ancongui/Downloads/resolicituddedocumentacin/2023.04.21_DF&IS_-_Pacto_de_Socios_Anexos_firmado.pdf")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

def deed_document_type() -> dict[str, Any]:
    """Schema for the Spanish incorporation deed (escritura de constitución)."""
    return {
        "id": "escritura_constitucion",
        "description": (
            "Spanish notarised incorporation deed (escritura de constitución) "
            "for a limited liability company (S.L. / S.A.). Includes founding "
            "shareholders, initial capital, registered office, and notary."
        ),
        "country": "ES",
        "field_groups": [
            {
                "name": "company",
                "description": "Identifying facts about the newly-formed entity.",
                "fields": [
                    {"name": "razon_social", "description": "Official company name (denominación social).", "type": "string", "required": True},
                    {"name": "forma_juridica", "description": "Legal form (e.g. S.L., S.A., S.L.U.).", "type": "string", "required": True},
                    {"name": "cif", "description": "Spanish corporate tax id (CIF / NIF).", "type": "string", "required": True,
                     "validators": [{"name": "cif", "severity": "warning"}]},
                    {"name": "domicilio_social", "description": "Full registered office address.", "type": "string", "required": True},
                    {"name": "capital_social_euros", "description": "Initial share capital amount in euros.", "type": "number", "required": True, "minimum": 0},
                    {"name": "moneda", "description": "Currency of the share capital (ISO 4217).", "type": "string",
                     "validators": [{"name": "currency_code", "severity": "warning"}]},
                    {"name": "fecha_constitucion", "description": "Date of incorporation.", "type": "string", "format": "date", "required": True},
                    {"name": "objeto_social", "description": "Stated business purpose / corporate object.", "type": "string"},
                    {"name": "duracion", "description": "Duration of the entity (typically 'indefinida').", "type": "string"},
                    {"name": "inicio_actividades", "description": "Start date of business activities.", "type": "string", "format": "date"},
                ],
            },
            {
                "name": "founders",
                "description": "Founding shareholders and their initial contributions.",
                "fields": [
                    {
                        "name": "founders",
                        "description": "One row per founding shareholder.",
                        "type": "array",
                        "items": {
                            "name": "founder",
                            "type": "object",
                            "fields": [
                                {"name": "nombre", "description": "Full legal name.", "type": "string"},
                                {"name": "tipo", "description": "'persona_fisica' or 'persona_juridica'.", "type": "string"},
                                {"name": "dni_nie_cif", "description": "Spanish DNI / NIE for individuals or CIF for entities.", "type": "string"},
                                {"name": "nacionalidad", "description": "Nationality (or country of incorporation).", "type": "string"},
                                {"name": "domicilio", "description": "Address (individual or registered office).", "type": "string"},
                                {"name": "aportacion_euros", "description": "Capital contribution in euros.", "type": "number", "minimum": 0},
                                {"name": "participaciones", "description": "Number of shares / participaciones issued.", "type": "integer", "minimum": 0},
                                {"name": "participacion_porcentaje", "description": "Percentage of the social capital owned.", "type": "number", "minimum": 0, "maximum": 100},
                            ],
                        },
                    },
                ],
            },
            {
                "name": "administrators",
                "description": "Initial board / administrators appointed at incorporation.",
                "fields": [
                    {
                        "name": "administrators",
                        "type": "array",
                        "items": {
                            "name": "administrator",
                            "type": "object",
                            "fields": [
                                {"name": "nombre", "type": "string"},
                                {"name": "dni_nie", "type": "string"},
                                {"name": "cargo", "description": "e.g. administrador unico, consejero, presidente.", "type": "string"},
                                {"name": "periodo_mandato", "description": "Term length (e.g. indefinido, 5 años).", "type": "string"},
                            ],
                        },
                    },
                ],
            },
            {
                "name": "notary",
                "description": "Notary execution metadata.",
                "fields": [
                    {"name": "notario_nombre", "description": "Full name of the notary public.", "type": "string"},
                    {"name": "notaria_localidad", "description": "City where the notary's office is located.", "type": "string"},
                    {"name": "numero_protocolo", "description": "Notary protocol number assigned to this deed.", "type": "string"},
                    {"name": "fecha_otorgamiento", "description": "Date the deed was executed before the notary.", "type": "string", "format": "date"},
                    {"name": "registro_mercantil", "description": "Mercantile Registry where the company is filed.", "type": "string"},
                    {"name": "tomo", "description": "Registry volume.", "type": "string"},
                    {"name": "folio", "description": "Registry folio.", "type": "string"},
                    {"name": "hoja", "description": "Registry sheet (hoja).", "type": "string"},
                    {"name": "inscripcion", "description": "Inscription number.", "type": "string"},
                ],
            },
        ],
        "visual_checks": [
            {"name": "firma_notario", "description": "The notary's handwritten or electronic signature is present."},
            {"name": "sello_notarial", "description": "The notarial seal (sello) is visible."},
            {"name": "sello_registro_mercantil", "description": "A Mercantile Registry stamp / inscripción seal is visible."},
        ],
    }


def pacto_document_type() -> dict[str, Any]:
    """Schema for the shareholders agreement (pacto de socios)."""
    return {
        "id": "pacto_socios",
        "description": (
            "Shareholders agreement (pacto de socios) detailing governance, "
            "share-transfer restrictions, tag-along / drag-along rights, and "
            "the parties signing it."
        ),
        "country": "ES",
        "field_groups": [
            {
                "name": "agreement",
                "fields": [
                    {"name": "fecha_firma", "description": "Date the agreement was signed.", "type": "string", "format": "date", "required": True},
                    {"name": "company_referenced", "description": "Razón social of the company the agreement governs.", "type": "string", "required": True},
                    {"name": "company_cif", "description": "CIF of the referenced company.", "type": "string",
                     "validators": [{"name": "cif", "severity": "warning"}]},
                    {"name": "vigencia", "description": "Duration / term of the agreement.", "type": "string"},
                    {"name": "ley_aplicable", "description": "Governing law clause.", "type": "string"},
                ],
            },
            {
                "name": "parties",
                "description": "Parties signing the agreement.",
                "fields": [
                    {
                        "name": "parties",
                        "type": "array",
                        "items": {
                            "name": "party",
                            "type": "object",
                            "fields": [
                                {"name": "nombre", "type": "string"},
                                {"name": "tipo", "description": "'persona_fisica' or 'persona_juridica'.", "type": "string"},
                                {"name": "dni_nie_cif", "type": "string"},
                                {"name": "rol", "description": "Role in the agreement (e.g. socio, fundador, inversor).", "type": "string"},
                                {"name": "participacion_porcentaje", "description": "Stake percentage attributed to this party.", "type": "number", "minimum": 0, "maximum": 100},
                            ],
                        },
                    },
                ],
            },
            {
                "name": "governance",
                "description": "Key governance + transfer mechanics.",
                "fields": [
                    {"name": "tag_along", "description": "Tag-along right declared.", "type": "boolean"},
                    {"name": "drag_along", "description": "Drag-along right declared.", "type": "boolean"},
                    {"name": "derecho_preferente", "description": "Pre-emption right (derecho de adquisición preferente).", "type": "boolean"},
                    {"name": "lock_up_periodo", "description": "Lock-up period for share transfers (if any).", "type": "string"},
                    {"name": "quorum_junta", "description": "Quorum required for shareholder meetings.", "type": "string"},
                    {"name": "mayoria_reforzada_materias", "description": "Matters requiring a reinforced majority.", "type": "string"},
                ],
            },
        ],
        "visual_checks": [
            {"name": "firmas_partes", "description": "Handwritten or electronic signatures from every party are present."},
        ],
    }


def kyb_rules() -> list[dict[str, Any]]:
    """KYB cross-document rules."""
    return [
        {
            "id": "kyb_company_complete",
            "predicate": (
                "The escritura_constitucion has a non-empty razon_social, CIF, domicilio_social, "
                "capital_social_euros and fecha_constitucion."
            ),
            "parents": [
                {
                    "kind": "field",
                    "document_type": "escritura_constitucion",
                    "fields": ["razon_social", "cif", "domicilio_social", "capital_social_euros", "fecha_constitucion"],
                }
            ],
            "output": {"type": "boolean", "valid_outputs": ["true", "false"]},
        },
        {
            "id": "founders_capital_matches",
            "predicate": (
                "Sum of founders.aportacion_euros equals the company.capital_social_euros within 0.01 euros."
            ),
            "parents": [
                {
                    "kind": "field",
                    "document_type": "escritura_constitucion",
                    "fields": ["capital_social_euros", "founders"],
                }
            ],
            "output": {"type": "boolean", "valid_outputs": ["true", "false"]},
        },
        {
            "id": "notary_complete",
            "predicate": (
                "The notary block has a non-empty notario_nombre, numero_protocolo and fecha_otorgamiento."
            ),
            "parents": [
                {
                    "kind": "field",
                    "document_type": "escritura_constitucion",
                    "fields": ["notario_nombre", "numero_protocolo", "fecha_otorgamiento"],
                }
            ],
            "output": {"type": "boolean", "valid_outputs": ["true", "false"]},
        },
        {
            "id": "pacto_signed",
            "predicate": "The pacto_socios has a non-empty fecha_firma and at least one party.",
            "parents": [
                {
                    "kind": "field",
                    "document_type": "pacto_socios",
                    "fields": ["fecha_firma", "parties"],
                }
            ],
            "output": {"type": "boolean", "valid_outputs": ["true", "false"]},
        },
        {
            "id": "shareholders_consistent",
            "predicate": (
                "Every party in pacto_socios.parties whose tipo is 'persona_fisica' or 'persona_juridica' "
                "is also present (by nombre OR dni_nie_cif) in escritura_constitucion.founders. "
                "Output 'true' when every pacto party is reconciled; 'partial' when at least one is missing "
                "but some match; 'false' when none match."
            ),
            "parents": [
                {"kind": "field", "document_type": "escritura_constitucion", "fields": ["founders"]},
                {"kind": "field", "document_type": "pacto_socios", "fields": ["parties"]},
            ],
            "output": {"type": "string", "valid_outputs": ["true", "partial", "false"]},
        },
        {
            "id": "kyb_ready",
            "predicate": "All of kyb_company_complete, notary_complete, pacto_signed are true.",
            "parents": [
                {"kind": "rule", "rule": "kyb_company_complete"},
                {"kind": "rule", "rule": "notary_complete"},
                {"kind": "rule", "rule": "pacto_signed"},
            ],
            "output": {"type": "boolean", "valid_outputs": ["true", "false"]},
        },
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def encode_pdf(path: Path) -> dict[str, Any]:
    return {
        "filename": path.name,
        "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        "content_type": "application/pdf",
    }


def summarise_field_group(group: dict[str, Any], depth: int = 0) -> None:
    indent = "  " * depth
    print(f"{indent}[{group['name']}]")
    for field in group.get("fields", []):
        val = field.get("value")
        if isinstance(val, list):
            print(f"{indent}  - {field['name']}: array<{len(val)} row(s)>")
            if val and isinstance(val[0], dict) and "value" in val[0]:
                for i, row in enumerate(val[:3]):
                    row_val = row.get("value")
                    if isinstance(row_val, list):
                        cells = ", ".join(f"{c.get('name')}={c.get('value')!r}" for c in row_val[:6])
                        print(f"{indent}      [{i}] {cells}")
        else:
            judge = field.get("judge") or {}
            validation = field.get("validation") or {}
            tag = ""
            if judge.get("status") == "fail":
                tag = "  [JUDGE: FAIL]"
            elif judge.get("flag_for_review"):
                tag = "  [REVIEW]"
            if validation.get("valid") is False:
                tag += "  [VALIDATION ERROR]"
            print(f"{indent}  - {field['name']}: {val!r}{tag}")


def summarise_result(result: dict[str, Any]) -> None:
    print("\n" + "=" * 78)
    print(f"Result id: {result.get('id')}")
    print(f"Status:    {result.get('status')}")
    print(f"Pipeline:")
    pipeline = result.get("pipeline") or {}
    print(f"  model:      {pipeline.get('model')}")
    print(f"  latency_ms: {pipeline.get('latency_ms')}")
    usage = pipeline.get("usage") or {}
    if usage:
        print(f"  usage:      {usage.get('total_tokens', 0):,} tokens, ${usage.get('total_cost_usd', 0):.4f} USD")
    errors = pipeline.get("errors") or []
    if errors:
        print(f"  errors:     {len(errors)} non-fatal stage failures")
        for err in errors[:5]:
            print(f"    - {err.get('node')}: {err.get('code')} -- {err.get('message')[:100]}")
    print()

    files = result.get("files") or []
    print(f"Files ({len(files)}):")
    for f in files:
        print(f"  - {f['filename']}: {f.get('media_type')}, {f.get('page_count')} pages, {f.get('bytes')} bytes -> {f.get('matched_type')}")
    print()

    docs = result.get("documents") or []
    print(f"Documents ({len(docs)}):")
    for d in docs:
        print(f"\n--- {d.get('type')} (source: {d.get('source_file')}, missing={d.get('missing')}, confidence={d.get('confidence')}) ---")
        for g in d.get("field_groups", []):
            summarise_field_group(g)

    rules = result.get("rule_results") or []
    if rules:
        print(f"\nRule results ({len(rules)}):")
        for r in rules:
            print(f"  - {r.get('rule_id')}: {r.get('output')!r}")
            if r.get("summary"):
                print(f"      {r['summary']}")

    print("=" * 78)


# ---------------------------------------------------------------------------
# Sync test
# ---------------------------------------------------------------------------

def test_sync() -> None:
    print("\n##### SYNC TEST -- POST /api/v1/extract ##############################")
    print(f"File: {PACTO_PDF.name} ({PACTO_PDF.stat().st_size:,} bytes)")
    request = {
        "intention": "KYB sync test: extract shareholders agreement key facts from the pacto de socios.",
        "files": [{**encode_pdf(PACTO_PDF), "expected_type": "pacto_socios"}],
        "document_types": [pacto_document_type()],
        "rules": [],
        "options": {
            "model": "anthropic:claude-sonnet-4-6",
            "language_hint": "es",
            "stages": {
                "splitter": False,
                "classifier": False,  # pinned
                "field_validation": True,
                "visual_authenticity": False,
                "content_authenticity": False,
                "judge": False,
                "judge_escalation": False,
                "bbox_refine": False,
                "transform": False,
                "rule_engine": False,
            },
        },
    }
    start = time.time()
    with httpx.Client(timeout=httpx.Timeout(90.0)) as client:
        resp = client.post(f"{API}/api/v1/extract", json=request)
    elapsed = time.time() - start
    print(f"HTTP {resp.status_code} in {elapsed:.1f}s")
    if resp.status_code != 200:
        print(f"Body: {resp.text[:2000]}")
        sys.exit(1)
    summarise_result(resp.json())


# ---------------------------------------------------------------------------
# Async test
# ---------------------------------------------------------------------------

def test_async() -> None:
    print("\n##### ASYNC TEST -- POST /api/v1/extractions #########################")
    print(f"Files: {DEED_PDF.name} ({DEED_PDF.stat().st_size:,} bytes)")
    print(f"       {PACTO_PDF.name} ({PACTO_PDF.stat().st_size:,} bytes)")
    request = {
        "intention": (
            "KYB end-to-end pack: extract the Spanish incorporation deed AND the shareholders agreement, "
            "judge the verdicts, and evaluate KYB readiness rules across the two documents."
        ),
        "files": [
            {**encode_pdf(DEED_PDF), "expected_type": "escritura_constitucion"},
            {**encode_pdf(PACTO_PDF), "expected_type": "pacto_socios"},
        ],
        "document_types": [deed_document_type(), pacto_document_type()],
        "rules": kyb_rules(),
        "options": {
            "model": "anthropic:claude-sonnet-4-6",
            "language_hint": "es",
            "stages": {
                "splitter": False,
                "classifier": False,  # both pinned
                "field_validation": True,
                "visual_authenticity": True,
                "content_authenticity": False,
                "judge": True,
                "judge_escalation": False,
                "bbox_refine": True,
                "transform": False,
                "rule_engine": True,
            },
        },
        "metadata": {"caller": "kyb_real_test", "scenario": "kyb_deed_plus_pacto"},
    }
    print(f"Submitting {len(json.dumps(request)):,} bytes of JSON...")
    with httpx.Client(timeout=httpx.Timeout(60.0)) as client:
        resp = client.post(f"{API}/api/v1/extractions", json=request)
    if resp.status_code != 202:
        print(f"Submit failed: HTTP {resp.status_code}")
        print(resp.text[:2000])
        sys.exit(1)
    ext = resp.json()
    ext_id = ext["id"]
    print(f"Submitted: id={ext_id}, status={ext['status']}, submitted_at={ext.get('submitted_at')}")

    # Poll until terminal main status.
    print("Polling main lifecycle...")
    start = time.time()
    last_status = None
    last_post = None
    while True:
        with httpx.Client(timeout=httpx.Timeout(30.0)) as client:
            r = client.get(f"{API}/api/v1/extractions/{ext_id}")
        if r.status_code != 200:
            print(f"poll: HTTP {r.status_code}: {r.text[:300]}")
            time.sleep(2)
            continue
        state = r.json()
        st = state.get("status")
        post = (state.get("post_processing") or {}).get("bbox_refinement") if state.get("post_processing") else None
        post_status = post.get("status") if post else None
        if st != last_status or post_status != last_post:
            elapsed = time.time() - start
            line = f"[{elapsed:5.1f}s] status={st}"
            if post_status:
                line += f", bbox_refinement={post_status}"
            print(line)
            last_status = st
            last_post = post_status
        if st in ("failed", "cancelled"):
            print(f"Terminal failure: {state.get('error')}")
            sys.exit(1)
        if st == "succeeded":
            # Wait for post_processing to also be terminal (or absent).
            if post_status in (None, "succeeded", "failed"):
                break
        if time.time() - start > 900:
            print("TIMEOUT after 15 minutes")
            sys.exit(1)
        time.sleep(3)

    elapsed = time.time() - start
    print(f"Reached terminal state in {elapsed:.1f}s")

    # Fetch result envelope.
    with httpx.Client(timeout=httpx.Timeout(60.0)) as client:
        r = client.get(f"{API}/api/v1/extractions/{ext_id}/result")
    if r.status_code != 200:
        print(f"result fetch failed: HTTP {r.status_code}: {r.text[:300]}")
        sys.exit(1)
    envelope = r.json()
    summarise_result(envelope["result"])


def main() -> None:
    if not DEED_PDF.exists():
        print(f"missing: {DEED_PDF}")
        sys.exit(1)
    if not PACTO_PDF.exists():
        print(f"missing: {PACTO_PDF}")
        sys.exit(1)

    test_sync()
    test_async()


if __name__ == "__main__":
    main()
