# Docling integration

Optional, layout-aware augmentations for the bbox refiner and the
multimodal extractor, backed by IBM's [Docling](https://github.com/docling-project/docling)
(LF AI&Data Foundation, MIT).

> **Read time**: 8 minutes. **Scope**: how to enable it, what it
> changes, and what to watch out for.

---

## 1. What it adds, in one paragraph

flydesk-idp's bbox refiner already grounds LLM-estimated coordinates
against the document's text layer (PyMuPDF for born-digital PDFs) and
against Tesseract OCR for image-PDFs / rasters. Docling slots into the
same `OcrEngine` protocol as **a third option** — but instead of flat
per-pixel OCR, it runs the **Heron** layout model **before** OCR. Text
regions, tables, and figures are detected first; OCR then runs only on
real text regions; tables are returned with explicit row/column
structure; every text item carries a **reading order**. The bbox
refiner picks up the cleaner words; the value matcher picks up
reading-order tie-breaks. Independently, a second integration point
(`DoclingTextAnchor`) renders the same document as Markdown and the
extractor splices that text into the user prompt alongside the binary
content — giving the multimodal LLM two modalities to cross-reference.

Both integrations are **opt-in** and gated by the `docling` extra in
`pyproject.toml`, so the slim image stays small.

---

## 2. The two integration points

### 2a. `DoclingOcrEngine` — layout-aware bbox refinement

Implements the same `OcrEngine` protocol as `NoneOcrEngine` and
`TesseractOcrEngine`, so it drops into the existing `WordRouter` →
`BboxRefiner` flow without changing any other stage.

What it produces, per page:

- Word-level `PageWords` with bboxes normalised to `[0, 1]` top-left
  image-space — same contract as the legacy engines.
- `Word.reading_order` (per-page monotonic counter) on every emitted
  token.
- `Word.table_id` / `row_idx` / `col_idx` on cells that came out of a
  Docling `TableItem`.
- `PageWords.source = ocr` (consistent with Tesseract; the refiner's
  idempotency rule treats both as "already grounded").

The value matcher consumes the new metadata transparently:
`ValueMatcher._match_spaced` / `_match_unspaced` break score ties by
**lowest `reading_order`**. When the same value appears in a header
and in body text, the header (earlier in document) wins. Legacy
engines that don't populate `reading_order` are unchanged.

### 2b. `DoclingTextAnchor` — pre-extraction Markdown anchor

Renders the document through Docling's Markdown exporter and the
extractor splices the result between the user instruction and the
`BinaryContent` block. Layout is::

    [user_text, anchor (if any), BinaryContent]

The LLM sees the cleaned-up textual representation of the page right
next to the actual image, so it can cross-reference layout against
text — measurably helpful on:

- Multilingual scans where the vision model misreads diacritics.
- Long PDFs where attention truncates.
- Dense tabular documents where row/column structure is hard to
  follow visually.

Failure modes are deliberate:

- **Missing dep** (`docling` not installed): hard `RuntimeError` at
  first use. A configuration error is louder than silent fallback.
- **Convert error** at runtime (model load fails, document poison):
  the anchor logs a warning and returns `None`. Extraction continues
  with binary-only behaviour — the anchor is a best-effort
  enrichment, it never blocks the pipeline.
- **Output bigger than the configured ceiling**: truncated on a
  paragraph boundary with a visible `[anchor truncated]` sentinel so
  the LLM knows the view was clipped.

---

## 3. Enabling it

### 3a. Local development

```bash
uv sync --extra docling --extra dev
export FLYDESK_IDP_BBOX_REFINE_OCR_ENGINE=docling
export FLYDESK_IDP_EXTRACTION_TEXT_ANCHOR=docling
task serve
```

Both knobs are independent — you can run Docling OCR without the
anchor (cheap layout grounding only), or just the anchor without
Docling OCR (cheap multimodal cross-reference only).

### 3b. Production: pulling the prebuilt image

The CI publishes two image variants on every push to `main` and on
every SemVer tag:

| Tag pattern | Architectures | What's in it | Pull |
|---|---|---|---|
| `:latest`, `:vX.Y.Z`, `:sha-<short>` | `linux/amd64` + `linux/arm64` | **Slim**. Default `tesseract` OCR engine; no PyTorch. | `docker pull ghcr.io/firefly-operationos/flydesk-idp:latest` |
| `:docling-latest`, `:docling-vX.Y.Z`, `:docling-sha-<short>` | `linux/amd64` **only** | **Docling**. Heavy variant with PyTorch + HF models baked in. Multi-arch publish doesn't fit a default GHA runner's disk; arm64 users can `buildx build --platform linux/arm64 --build-arg WITH_DOCLING=true` locally. | `docker pull ghcr.io/firefly-operationos/flydesk-idp:docling-latest` |

Set the env vars in the docling variant and you're done:

```yaml
# docker-compose override
api:
  image: ghcr.io/firefly-operationos/flydesk-idp:docling-latest
  environment:
    FLYDESK_IDP_BBOX_REFINE_OCR_ENGINE: docling
    FLYDESK_IDP_EXTRACTION_TEXT_ANCHOR: docling
```

### 3c. Building the image yourself

```bash
docker buildx build \
    --build-arg WITH_DOCLING=true \
    --build-context pyfly=../../fireflyframework/fireflyframework-pyfly \
    --build-context fireflyframework-agentic=../../fireflyframework/fireflyframework-agentic \
    --tag flydesk-idp:docling \
    .
```

`WITH_DOCLING=false` (default) keeps the image small.

---

## 4. Settings reference

| Env var | Default | Effect |
|---|---|---|
| `FLYDESK_IDP_BBOX_REFINE_OCR_ENGINE` | `tesseract` | `docling` swaps in `DoclingOcrEngine` for image-PDFs + rasters. `none` keeps the LLM bbox. `tesseract` is the legacy default. |
| `FLYDESK_IDP_EXTRACTION_TEXT_ANCHOR` | `none` | `docling` splices a Markdown anchor into every extract / extract-retry call. |
| `FLYDESK_IDP_EXTRACTION_TEXT_ANCHOR_MAX_CHARS` | `12000` | Hard ceiling on the anchor length. Truncated on a paragraph boundary when in reach, otherwise hard-cut with a visible sentinel. Set to `0` to silently disable even when the engine is configured. |

Existing knobs that still apply:

- `FLYDESK_IDP_BBOX_REFINE_THRESHOLD` (default `0.85`) — fuzz score floor.
- `FLYDESK_IDP_BBOX_REFINE_MIN_TEXT_WORDS` (default `5`) — per-page
  threshold below which the page is treated as image-only and routed
  to the configured OCR engine.
- `FLYDESK_IDP_BBOX_REFINE_OCR_DPI` (default `200`) — only used by
  the Tesseract path. Docling rasterises internally.
- `FLYDESK_IDP_BBOX_REFINE_TESSERACT_LANG` (default `spa+eng`) — also
  consumed by Docling as the default OCR language hint when no
  per-request `language_hint` is supplied.

---

## 5. Trade-offs to be aware of

### 5a. Cold start + memory

Docling lazy-loads the Heron layout model and the configured OCR
backend (RapidOCR by default in v2.93+) on the first `recognise()` /
`produce()` call. Expect **30-60 seconds** on a clean machine while
weights download, then seconds per page on warm calls. Memory
footprint sits around **2-3 GB resident** with both models loaded on
CPU.

For long-running workers this is a one-time cost. For short-lived /
serverless deployments, pre-warm by running a tiny synthetic PDF
through the engine at boot, or set
`FLYDESK_IDP_BBOX_REFINE_OCR_ENGINE=tesseract` and only flip
`FLYDESK_IDP_EXTRACTION_TEXT_ANCHOR=docling` for the requests where
the anchor is worth the wait.

### 5b. Prompt cache hit-rate

Anthropic prompt caching keys on the exact byte prefix of
`(instructions + tools + messages)`. The text anchor sits in the user
message and changes per document, so it **moves the cache boundary**.
Two follow-ups:

- Cache hit-rate drops on documents that previously shared a system
  prompt prefix — the warm-cache premium increases relative to the
  binary-only path. Measure before flipping on for high-volume
  workloads.
- `FLYDESK_IDP_PROMPT_CACHE=off` disables Anthropic prompt caching
  entirely; useful for A/B comparison.

See [`pipeline.md` § 7c](pipeline.md#7c-pricing--prompt-caching) for
the full caching discussion.

### 5c. Async path

The `JobWorker` already mutates `stages.bbox_refine = False` before
calling the orchestrator, then publishes `IDPBboxRefineRequested` for
the dedicated `BboxRefineWorker`. With Docling as the OCR engine the
refiner runs out-of-band — same as Tesseract — so async submitters
get a `PARTIAL_SUCCEEDED → SUCCEEDED` transition once Docling has
finished grounding bboxes. Idempotent on re-run because the refiner
skips fields with `bbox.source ∈ {pdf_text, ocr}`.

### 5d. Distroless deployment

The `docling` variant is **not** distroless-friendly — PyTorch and
the HF model loaders pull in `libstdc++` and a writable
`~/.cache/docling`. Stay on the slim image for distroless deployments
and run Docling features only in pods built from the `docling`
variant.

---

## 6. Running the real integration tests locally

```bash
uv sync --extra docling --extra dev
uv run pytest tests/integration/test_docling_real.py -v
```

The tests synthesize tiny PDFs inline with reportlab and exercise:

- Word emission with normalised top-left coords (`source=ocr`,
  `reading_order` populated).
- Multi-page word streams.
- Per-page reading-order monotonicity.
- Text-anchor Markdown export + truncation behaviour.
- Determinism across runs.

The first run downloads ~50 MB of model weights into `~/.cache/`.
Subsequent runs are seconds.

---

## 7. Source pointers

| Component | File |
|---|---|
| OCR engine | [`src/flydesk_idp/core/services/bbox/docling_engine.py`](../src/flydesk_idp/core/services/bbox/docling_engine.py) |
| Text anchor | [`src/flydesk_idp/core/services/extraction/text_anchor.py`](../src/flydesk_idp/core/services/extraction/text_anchor.py) |
| `Word` metadata | [`src/flydesk_idp/core/services/bbox/word_extractor.py`](../src/flydesk_idp/core/services/bbox/word_extractor.py) |
| Matcher tie-break | [`src/flydesk_idp/core/services/bbox/value_matcher.py`](../src/flydesk_idp/core/services/bbox/value_matcher.py) |
| Settings | [`src/flydesk_idp/config.py`](../src/flydesk_idp/config.py) — search for `bbox_refine_ocr_engine` + `extraction_text_anchor` |
| DI wiring | [`src/flydesk_idp/core/configuration.py`](../src/flydesk_idp/core/configuration.py) — `ocr_engine` + `text_anchor` beans |
| Unit tests | [`tests/unit/test_docling_engine.py`](../tests/unit/test_docling_engine.py), [`tests/unit/test_text_anchor.py`](../tests/unit/test_text_anchor.py), [`tests/unit/test_value_matcher.py`](../tests/unit/test_value_matcher.py) |
| Integration tests | [`tests/integration/test_docling_real.py`](../tests/integration/test_docling_real.py) |

## 8. Roadmap

Things explicitly out of scope for this initial integration that we
may add later as the operational experience matures:

- **Table-aware matching by structure**, not just by text — the
  matcher currently treats table cells like any other word; it could
  prefer in-table grounding when the target field is part of an
  array row.
- **Shared converter bean** so `DoclingOcrEngine` and
  `DoclingTextAnchor` reuse one loaded model instead of constructing
  two.
- **Granite-docling VLM mode** as an alternative to Heron + RapidOCR
  on documents where the dedicated VLM noticeably outperforms.
- **Picture description / formula enrichment** for documents where
  signatures, stamps, or equations carry meaning.
