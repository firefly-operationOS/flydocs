#!/usr/bin/env python3
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

"""End-to-end smoke + bbox visualizer.

Sends two real documents (escritura + DNI) through ``POST /api/v1/extract``
with ``stages.bbox_refine=true``, then renders each page with the
returned bboxes overlaid so the bbox refiner's output can be eyeballed.

Output: ``/tmp/flydocs-viz/index.html`` plus one PNG per page.
Open in a browser to see the bboxes coloured by source:

* green   -- ``source=pdf_text`` (grounded via PyMuPDF text layer).
* orange  -- ``source=ocr`` (grounded via the OCR engine).
* red     -- ``source=llm`` (LLM estimate, no fuzzy match found).
* gray    -- ``source=none`` (no value / empty placeholder).

Usage:
    .venv/bin/python scripts/smoke_bbox_real.py
"""

from __future__ import annotations

import base64
import html
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pymupdf  # pyright: ignore[reportMissingImports]
from PIL import Image, ImageDraw, ImageFont

API = "http://localhost:8400"
OUT = Path("/tmp/flydocs-viz")
OUT.mkdir(parents=True, exist_ok=True)

# RGB outlines per bbox source.
COLORS = {
    "pdf_text": (0, 170, 0),
    "ocr": (255, 140, 0),
    "llm": (220, 50, 50),
    "none": (160, 160, 160),
    None: (160, 160, 160),
}
SOURCE_LABELS = {
    "pdf_text": "grounded (pdf text)",
    "ocr": "grounded (ocr)",
    "llm": "llm estimate",
    "none": "empty",
}


# ---------------------------------------------------------------------------
# Request payloads
# ---------------------------------------------------------------------------


def _escritura_request(pdf_bytes: bytes, filename: str) -> dict[str, Any]:
    """Spanish notarial power-of-attorney schema."""
    return {
        "intention": (
            "Audit a Spanish notarial power of attorney for KYC purposes. "
            "Extract the canonical fields and locate them precisely on the page."
        ),
        "document": {
            "filename": filename,
            "content_base64": base64.b64encode(pdf_bytes).decode(),
            "content_type": "application/pdf",
        },
        "docs": [
            {
                "docType": {
                    "documentType": "escritura_poderes",
                    "description": "Escritura notarial de poderes",
                    "country": "ES",
                },
                "fieldGroups": [
                    {
                        "fieldGroupName": "otorgamiento",
                        "fieldGroupFields": [
                            {
                                "fieldName": "numero_protocolo",
                                "fieldDescription": "Numero de protocolo notarial.",
                                "fieldType": "string",
                            },
                            {
                                "fieldName": "fecha",
                                "fieldDescription": "Fecha del otorgamiento (ISO YYYY-MM-DD).",
                                "fieldType": "string",
                                "standard_validators": [{"type": "date"}],
                            },
                            {
                                "fieldName": "notario",
                                "fieldDescription": "Nombre completo del notario.",
                                "fieldType": "string",
                            },
                            {
                                "fieldName": "otorgante_nombre",
                                "fieldDescription": "Nombre completo del otorgante (poderdante).",
                                "fieldType": "string",
                            },
                            {
                                "fieldName": "otorgante_dni_nie",
                                "fieldDescription": "DNI o NIE del otorgante.",
                                "fieldType": "string",
                                "standard_validators": [
                                    {"type": "nif", "severity": "warning"},
                                    {"type": "nie", "severity": "warning"},
                                ],
                            },
                            {
                                "fieldName": "apoderado_nombre",
                                "fieldDescription": "Nombre completo del apoderado.",
                                "fieldType": "string",
                            },
                            {
                                "fieldName": "apoderado_dni_nie",
                                "fieldDescription": "DNI o NIE del apoderado.",
                                "fieldType": "string",
                                "standard_validators": [
                                    {"type": "nif", "severity": "warning"},
                                    {"type": "nie", "severity": "warning"},
                                ],
                            },
                        ],
                    }
                ],
            }
        ],
        "rules": [],
        "options": {
            "model": "anthropic:claude-sonnet-4-6",
            "language_hint": "es",
            "stages": {
                "splitter": False,
                "field_validation": True,
                "visual_authenticity": False,
                "content_authenticity": False,
                "judge": False,
                "rule_engine": False,
                "bbox_refine": True,
            },
        },
    }


def _dni_request(pdf_bytes: bytes, filename: str) -> dict[str, Any]:
    """Spanish national ID card schema -- front (page 1) + back (page 2)."""
    return {
        "intention": (
            "Extract every printed field from a Spanish DNI/eID for KYC. "
            "Both sides of the card are present: front carries identity, "
            "back carries domicile, place of birth, parents, equipo, MRZ."
        ),
        "document": {
            "filename": filename,
            "content_base64": base64.b64encode(pdf_bytes).decode(),
            "content_type": "application/pdf",
        },
        "docs": [
            {
                "docType": {
                    "documentType": "dni",
                    "description": "Documento Nacional de Identidad espanol",
                    "country": "ES",
                },
                "fieldGroups": [
                    {
                        "fieldGroupName": "identidad",
                        "fieldGroupFields": [
                            {
                                "fieldName": "dni_numero",
                                "fieldDescription": "Numero del DNI/NIE (con letra).",
                                "fieldType": "string",
                                "standard_validators": [
                                    {"type": "nif", "severity": "warning"},
                                    {"type": "nie", "severity": "warning"},
                                ],
                            },
                            {
                                "fieldName": "nombre",
                                "fieldDescription": "Nombre de pila.",
                                "fieldType": "string",
                            },
                            {
                                "fieldName": "apellidos",
                                "fieldDescription": "Apellidos completos.",
                                "fieldType": "string",
                            },
                            {
                                "fieldName": "fecha_nacimiento",
                                "fieldDescription": "Fecha de nacimiento (ISO YYYY-MM-DD).",
                                "fieldType": "string",
                                "standard_validators": [{"type": "date"}],
                            },
                            {
                                "fieldName": "sexo",
                                "fieldDescription": "Sexo (M/F).",
                                "fieldType": "string",
                            },
                            {
                                "fieldName": "nacionalidad",
                                "fieldDescription": "Nacionalidad (codigo o nombre).",
                                "fieldType": "string",
                            },
                        ],
                    },
                    {
                        "fieldGroupName": "vigencia",
                        "fieldGroupFields": [
                            {
                                "fieldName": "fecha_emision",
                                "fieldDescription": (
                                    "Fecha de emision / issuing date (ISO YYYY-MM-DD). "
                                    "Aparece bajo el campo 'EMISION' o 'IDESP' en el anverso."
                                ),
                                "fieldType": "string",
                                "standard_validators": [{"type": "date"}],
                            },
                            {
                                "fieldName": "fecha_caducidad",
                                "fieldDescription": (
                                    "Fecha de caducidad / expiration date (ISO YYYY-MM-DD). "
                                    "Aparece bajo el campo 'VALIDEZ'."
                                ),
                                "fieldType": "string",
                                "standard_validators": [{"type": "date"}],
                            },
                            {
                                "fieldName": "numero_soporte",
                                "fieldDescription": (
                                    "Numero de soporte / support number, prefijo 'CDD' o 'IDESP' "
                                    "seguido de digitos. Aparece bajo 'NUM SOPORTE'."
                                ),
                                "fieldType": "string",
                            },
                            {
                                "fieldName": "can",
                                "fieldDescription": (
                                    "CAN (Card Access Number), 6 digitos numericos impresos "
                                    "discretamente cerca del numero de soporte."
                                ),
                                "fieldType": "string",
                            },
                        ],
                    },
                    {
                        "fieldGroupName": "domicilio_y_filiacion",
                        "fieldGroupFields": [
                            {
                                "fieldName": "domicilio",
                                "fieldDescription": (
                                    "Domicilio completo tal como figura en el reverso de la "
                                    "tarjeta (calle, numero, piso, puerta)."
                                ),
                                "fieldType": "string",
                            },
                            {
                                "fieldName": "lugar_nacimiento",
                                "fieldDescription": (
                                    "Lugar de nacimiento (municipio y provincia) del reverso."
                                ),
                                "fieldType": "string",
                            },
                            {
                                "fieldName": "padre_nombre",
                                "fieldDescription": ("Nombre del padre (HIJO/A DE), reverso de la tarjeta."),
                                "fieldType": "string",
                            },
                            {
                                "fieldName": "madre_nombre",
                                "fieldDescription": (
                                    "Nombre de la madre (HIJO/A DE), reverso de la tarjeta."
                                ),
                                "fieldType": "string",
                            },
                            {
                                "fieldName": "equipo",
                                "fieldDescription": ("Codigo del equipo emisor (EQUIPO), letras y digitos."),
                                "fieldType": "string",
                            },
                        ],
                    },
                ],
            }
        ],
        "rules": [],
        "options": {
            "model": "anthropic:claude-sonnet-4-6",
            "language_hint": "es",
            "stages": {
                "splitter": False,
                "field_validation": True,
                "visual_authenticity": False,
                "content_authenticity": False,
                "judge": False,
                "rule_engine": False,
                "bbox_refine": True,
            },
        },
    }


# ---------------------------------------------------------------------------
# Visualizer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FieldBox:
    name: str
    value: str
    page: int
    bbox: tuple[float, float, float, float]
    source: str | None
    refinement_confidence: float | None
    confidence: float


def _walk_fields(groups: list[dict[str, Any]]) -> list[FieldBox]:
    """Flatten the field tree -- recurse into array rows -- into one list."""
    out: list[FieldBox] = []
    for group in groups:
        for field in group.get("fieldGroupFields", []):
            out.extend(_walk_field(field))
    return out


def _walk_field(field: dict[str, Any]) -> list[FieldBox]:
    value = field.get("fieldValueFound")
    if isinstance(value, list):
        nested: list[FieldBox] = []
        for child in value:
            if isinstance(child, dict):
                nested.extend(_walk_field(child))
        return nested
    bbox = field.get("bbox") or {}
    xmin = float(bbox.get("xmin", 0.0))
    ymin = float(bbox.get("ymin", 0.0))
    xmax = float(bbox.get("xmax", 0.0))
    ymax = float(bbox.get("ymax", 0.0))
    if xmax - xmin < 1e-6 or ymax - ymin < 1e-6:
        return []  # placeholder / empty bbox -- skip
    page = (field.get("pagesFound") or [1])[0]
    return [
        FieldBox(
            name=str(field.get("fieldName", "")),
            value=str(value) if value is not None else "",
            page=int(page),
            bbox=(xmin, ymin, xmax, ymax),
            source=bbox.get("source"),
            refinement_confidence=bbox.get("refinement_confidence"),
            confidence=float(field.get("confidence", 0.0)),
        )
    ]


def _font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=14)
        except OSError:
            continue
    return ImageFont.load_default()


def visualize(pdf_path: Path, response: dict[str, Any], out_prefix: str) -> list[Path]:
    """Render each PDF page with bboxes overlaid; return the PNG paths."""
    doc = pymupdf.open(pdf_path)
    documents = response.get("documents") or []
    field_boxes: list[FieldBox] = []
    for d in documents:
        field_boxes.extend(_walk_fields(d.get("fields") or []))
    by_page: dict[int, list[FieldBox]] = {}
    for fb in field_boxes:
        by_page.setdefault(fb.page, []).append(fb)
    font = _font()
    png_paths: list[Path] = []
    # Render every page in the document so the viewer can audit OCR /
    # text-layer coverage even on pages where no fields landed.
    for page_index in range(doc.page_count):
        page = doc[page_index]
        page_number = page_index + 1
        boxes = by_page.get(page_number, [])
        pix = page.get_pixmap(dpi=144)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        draw = ImageDraw.Draw(img, "RGBA")
        for fb in boxes:
            outline = COLORS.get(fb.source, COLORS[None])
            x0 = fb.bbox[0] * img.width
            y0 = fb.bbox[1] * img.height
            x1 = fb.bbox[2] * img.width
            y1 = fb.bbox[3] * img.height
            # Semi-transparent fill + solid outline.
            fill = (*outline, 40)
            draw.rectangle((x0, y0, x1, y1), outline=outline, width=3, fill=fill)
            # Label above the box (or below if near the top edge).
            text = f"{fb.name}: {fb.value[:48]}"
            if fb.refinement_confidence is not None:
                text += f"  (fuzz={fb.refinement_confidence:.2f})"
            text_y = y0 - 18 if y0 > 20 else y1 + 2
            text_bbox = draw.textbbox((x0, text_y), text, font=font)
            draw.rectangle(
                (text_bbox[0] - 2, text_bbox[1] - 2, text_bbox[2] + 2, text_bbox[3] + 2),
                fill=(255, 255, 255, 220),
            )
            draw.text((x0, text_y), text, fill=outline, font=font)
        out_path = OUT / f"{out_prefix}-page{page_number:03d}.png"
        img.save(out_path, format="PNG", optimize=True)
        png_paths.append(out_path)
    doc.close()
    return png_paths


# ---------------------------------------------------------------------------
# HTML index
# ---------------------------------------------------------------------------


def write_index(jobs: list[dict[str, Any]]) -> Path:
    parts: list[str] = [
        '<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>flydocs bbox viewer</title>',
        "<style>",
        "body{font-family:system-ui,sans-serif;margin:24px;background:#fafafa;color:#222}",
        "h1{margin:0 0 8px}h2{margin:24px 0 4px}",
        (
            ".legend span{display:inline-block;padding:2px 8px;margin-right:8px;"
            "border-radius:4px;font-size:12px}"
        ),
        ".legend .pdf{background:#dff7df;border:1px solid #00aa00}",
        ".legend .ocr{background:#ffebcc;border:1px solid #ff8c00}",
        ".legend .llm{background:#fde0e0;border:1px solid #dc3232}",
        ".legend .none{background:#eee;border:1px solid #a0a0a0}",
        ".meta{font-size:13px;color:#555;margin:4px 0 12px}",
        ".pages{display:flex;flex-wrap:wrap;gap:16px}",
        ".pages figure{margin:0;background:#fff;border:1px solid #ddd;padding:6px;border-radius:6px}",
        ".pages img{max-width:480px;height:auto;display:block}",
        ".pages figcaption{font-size:12px;color:#555;margin-top:4px;text-align:center}",
        "table{border-collapse:collapse;margin-top:8px;font-size:13px}",
        "table td,table th{border:1px solid #ddd;padding:4px 8px;text-align:left}",
        "table th{background:#f0f0f0}",
        "</style></head><body>",
        "<h1>flydocs bbox visualizer</h1>",
        '<div class="legend">',
        '<span class="pdf">grounded (pdf text)</span>',
        '<span class="ocr">grounded (ocr)</span>',
        '<span class="llm">llm estimate</span>',
        '<span class="none">empty</span>',
        "</div>",
    ]
    for job in jobs:
        parts.append(f"<h2>{html.escape(job['title'])}</h2>")
        usage = job.get("usage") or {}
        cost = usage.get("total_cost_usd") or 0.0
        latency = job.get("latency_ms")
        parts.append(
            f'<div class="meta">model: {html.escape(job.get("model", "?"))} &nbsp;|&nbsp; '
            f"latency: {latency} ms &nbsp;|&nbsp; cost: ${cost:.4f} &nbsp;|&nbsp; "
            f"grounded: {job['grounded_count']} / total: {job['total_count']} "
            f"&nbsp;|&nbsp; sources: {html.escape(job['source_summary'])}</div>"
        )
        rows = [
            "<table><tr><th>field</th><th>value</th><th>page</th><th>source</th><th>fuzz</th><th>conf</th></tr>"
        ]
        for fb in job["fields"]:
            rows.append(
                "<tr>"
                f"<td>{html.escape(fb.name)}</td>"
                f"<td>{html.escape(fb.value[:80])}</td>"
                f"<td>{fb.page}</td>"
                f"<td>{html.escape(SOURCE_LABELS.get(fb.source, str(fb.source)))}</td>"
                f"<td>{fb.refinement_confidence:.2f}</td>"
                if fb.refinement_confidence is not None
                else "<tr>"
                f"<td>{html.escape(fb.name)}</td>"
                f"<td>{html.escape(fb.value[:80])}</td>"
                f"<td>{fb.page}</td>"
                f"<td>{html.escape(SOURCE_LABELS.get(fb.source, str(fb.source)))}</td>"
                f"<td>-</td>"
            )
            rows.append(f"<td>{fb.confidence:.2f}</td></tr>")
        rows.append("</table>")
        parts.append("".join(rows))
        parts.append('<div class="pages">')
        for png in job["pngs"]:
            page_num = png.stem.split("-")[-1].replace("page", "")
            parts.append(
                f'<figure><img src="{png.name}" alt="page {page_num}">'
                f"<figcaption>page {page_num}</figcaption></figure>"
            )
        parts.append("</div>")
    parts.append("</body></html>")
    out = OUT / "index.html"
    out.write_text("".join(parts), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _summarise_sources(boxes: list[FieldBox]) -> str:
    counts: dict[str, int] = {}
    for fb in boxes:
        key = fb.source or "none"
        counts[key] = counts.get(key, 0) + 1
    return ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))


def _run_one(pdf_path: Path, builder, title: str, slug: str) -> dict[str, Any]:
    pdf_bytes = pdf_path.read_bytes()
    payload = builder(pdf_bytes, pdf_path.name)
    print(f"\n[{title}] POST /api/v1/extract  ({len(pdf_bytes) // 1024} KB)")
    started = time.monotonic()
    with httpx.Client(timeout=httpx.Timeout(600.0)) as client:
        r = client.post(f"{API}/api/v1/extract", json=payload)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if r.status_code >= 400:
        sys.stderr.write(f"[{title}] HTTP {r.status_code}: {r.text[:500]}\n")
        sys.exit(2)
    response = r.json()
    print(f"[{title}] HTTP 200, elapsed {elapsed_ms} ms (server: {response.get('latency_ms')} ms)")
    boxes = []
    for d in response.get("documents") or []:
        boxes.extend(_walk_fields(d.get("fields") or []))
    grounded = sum(1 for b in boxes if b.source in ("pdf_text", "ocr"))
    print(f"[{title}] fields={len(boxes)}, grounded={grounded}, sources=[{_summarise_sources(boxes)}]")
    # Persist raw response next to PNGs for debugging.
    (OUT / f"{slug}-response.json").write_text(
        json.dumps(response, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    pngs = visualize(pdf_path, response, slug)
    print(f"[{title}] rendered {len(pngs)} page PNG(s)")
    return {
        "title": title,
        "fields": boxes,
        "grounded_count": grounded,
        "total_count": len(boxes),
        "source_summary": _summarise_sources(boxes),
        "pngs": pngs,
        "model": response.get("model", "?"),
        "latency_ms": response.get("latency_ms"),
        "usage": response.get("usage"),
    }


def main() -> int:
    pdf_escritura = Path("/Users/ancongui/Downloads/escritura_poderes_2025.pdf")
    pdf_dni = Path("/Users/ancongui/Downloads/DNI ANDRES.pdf")
    if not pdf_escritura.exists() or not pdf_dni.exists():
        sys.stderr.write("[error] one or both PDFs are missing\n")
        return 2

    jobs = [
        _run_one(pdf_escritura, _escritura_request, "Escritura de poderes (ES)", "escritura"),
        _run_one(pdf_dni, _dni_request, "DNI andres (ES)", "dni"),
    ]
    index = write_index(jobs)
    print(f"\nopen {index} to view bboxes in a browser")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
