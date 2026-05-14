# The pipeline

This is the deep dive on `PipelineOrchestrator` and the stages it
runs. Read it when you're touching the orchestrator, adding a new
stage, or trying to understand why a stage didn't fire.

---

## 1. What the orchestrator is

The orchestrator is the only entry point from the CQRS layer into the
LLM pipeline. It is a plain pyfly bean (`@bean orchestrator` in
`IDPCoreConfiguration`) and exposes a single async method:

```python
async def execute(self, request: ExtractionRequest) -> ExtractionResult: ...
```

`execute` builds a fresh `agentic.PipelineEngine` DAG per request,
runs it, and assembles the result. The DAG nodes are selected from
`request.options.stages` so the trace and the event log reflect
exactly what executed.

> The method is called `execute` rather than `run` so it does **not**
> accidentally satisfy pyfly's `CommandLineRunner` structural protocol
> (which would auto-invoke `run(sys.argv[1:])` at startup).

---

## 2. The stages, at a glance

| Order | Stage               | Mandatory? | What it does                                                                                       | Default timeout |
| ----: | ------------------- | :--------: | -------------------------------------------------------------------------------------------------- | --------------: |
|     1 | `load`              | yes        | Sniff media type, count pages. Pure Python.                                                        | 10 s            |
|     2 | `split`             | no         | LLM identifies the page range of every target `docType` in a multi-doc file.                       | 60 s            |
|     3 | `extract`           | yes        | LLM produces fields + normalised bboxes for every target document.                                 | 240 s           |
|     4 | `field_validation`  | no         | Pure-Python validation: regex / enum / range + every `StandardValidator` declared per field.       | 5 s             |
|     5 | `visual_authenticity` | no       | LLM evaluates caller-defined visual validators (signature present, stamp present, …).              | 180 s           |
|     6 | `content_authenticity` | no      | LLM audit: dates consistent, totals add up, expected boilerplate, tampering signals.               | 180 s           |
|     7 | `judge`             | no         | Second LLM pass re-grades every extracted value against the source.                                 | 180 s           |
|     8 | `rules`             | no         | LLM evaluates the business-rule DAG, level by level.                                                | 180 s           |
|     9 | `assemble`          | yes        | Pure Python: compose the `ExtractionResult`.                                                       | 5 s             |

Stages 2–8 are caller-toggled through `ExtractionOptions.stages`. The
splitter is additionally short-circuited when there's only one
document type (no need to split) or the source is a single page.

---

## 3. How the DAG is built

```python
builder = PipelineBuilder("flydesk-idp")
builder.add_node("load",    CallableStep(self._step_load),    timeout_seconds=10)
if stages.splitter and len(request.docs) > 1:
    builder.add_node("split", CallableStep(self._step_split), timeout_seconds=60)
builder.add_node("extract", CallableStep(self._step_extract), timeout_seconds=240)
if stages.field_validation:
    builder.add_node("field_validation", ...)
...
builder.chain(*chain)        # linear order
engine = builder.build()
engine._event_handler = _LoggingEventHandler(str(request.request_id))
await engine.run(context=ctx)
```

Three properties fall out of this design:

- **The trace mirrors the request.** A request that disables `judge`
  produces a DAG without a `judge` node, and the event log shows the
  exact path executed.
- **Per-stage timeouts.** A slow LLM call doesn't drag the whole
  request beyond its sync timeout — only its stage.
- **Failure isolation.** A `CallableStep` that raises is captured by
  the engine; the orchestrator records the failure in
  `context.metadata["pipeline_errors"]` and moves on.

---

## 4. Stage-by-stage

### 4a. `load`

`core/services/extraction/loader.py::load_document` sniffs the media
type (magic bytes, then declared content type as a fallback) and
counts pages (`pypdf` for PDF; everything else is one page from the
extractor's point of view). Stored in `ctx.metadata["loaded"]`.

No LLM calls. Cheap.

### 4b. `split`

`core/services/splitting/splitter.py`. Shortcut for single-page or
single-target requests — no LLM call.

Otherwise: the document bytes go to the LLM along with the list of
target `docType`s. The LLM returns, for each target, either a page
range (`start`, `end` 1-indexed) or `missing: true`. Plus any
**additional** documents in the source that don't match a target (the
`additional_docs` list).

Page ranges are clamped to `[1, page_count]` and `start <= end` is
enforced before slicing. PDF slicing uses `pypdf` (see
`pdf_slicer.py`); non-PDF inputs are not sliced.

### 4c. `extract`

`core/services/extraction/extractor.py`. One LLM call per `DocSpec`,
fanned out with `asyncio.gather`.

For each call:

1. Build a dynamic Pydantic model from the `DocSpec` field schema
   (`build_extraction_output_model`). This is what `pydantic-ai` uses
   to enforce structured output.
2. Render the `flydesk_idp/extract` prompt with the JSON schema, media
   type, page count, intention, and optional language hint.
3. Call `FireflyAgent.run([prompt.user, BinaryContent(...)])` — the
   document bytes go inline as multimodal content.
4. Post-process the output with `normalise_doc` — clamp every bbox to
   `[0, 1]`, coerce types, populate `pagesFound`.

Fallback: if the primary model fails (timeout, content policy, etc.)
**and** `FLYDESK_IDP_FALLBACK_MODEL` is set to a different model, the
extractor retries on the fallback. The actual model used is reported
back in `per_doc_model_used` and reflected in `result.model`.

### 4d. `field_validation`

`core/services/validation/field_validator.py`. Pure Python — no LLM.

For every extracted field, run:

- **Type coercion** (string / number / integer / boolean / enum).
- **Per-field regex** if `FieldSpec.regex` is set.
- **Numeric range** if `FieldSpec.min` / `max` is set.
- **Enum membership** if `FieldSpec.enum` is set.
- **Every `StandardValidator`** declared in
  `FieldSpec.standard_validators`. The registry maps
  `StandardValidatorType` to a checker function; see
  [standard-validators.md](standard-validators.md).

Errors are recorded on the field's `field_validation.errors[]`. By
default a failure flips `field_validation.valid` to false; pass
`{"severity": "warning"}` to record the error without flipping the
flag.

### 4e. `visual_authenticity`

`core/services/authenticity/visual_validator.py`. One LLM call per
document.

Inputs:

- the document bytes (so the LLM can see the actual rendering),
- the list of caller-defined visual validators (each is just a
  `(name, description)` pair).

Output: one `VisualValidationOutcome` per validator with `passed`,
`confidence`, and `notes`.

Use this for visible-only checks (presence of a signature, a stamp, a
photo, an MRZ band, …) — not for semantic checks over field values.

### 4f. `content_authenticity`

`core/services/authenticity/content_validator.py`. One LLM call per
document. No caller-defined validators — the LLM picks the coherence
checks itself, guided by the document type, description, country, and
intention.

Output: a `ContentAuthenticity` aggregate with:

- `overall_integrity_status`: `VALID` / `INVALID` / `UNCERTAIN`,
- `checks[]`: each with `name`, `description`, `status`, `evidence`,
  `reasoning`.

Useful for "is this document internally consistent" checks where
hand-coding the list of validators is impractical.

### 4g. `judge`

`core/services/judge/judge.py`. One LLM call per document.

The judge sees:

- the document bytes (multimodal),
- the JSON dump of the extraction result.

For every field, it returns `status`, `confidence`, `evidence` (the
exact quote / region it matched), `notes`, and `flag_for_review`. The
service mutates the existing `ExtractedField`s in place by populating
their `judge` attribute.

The judge is **strict**: a value that's plausible but unsupported by
the document gets `status=FAIL`. This is the cheapest defence against
LLM hallucinations.

### 4h. `rules`

`core/services/rules/rule_engine.py`. The business-rule DAG.

- Nodes are rule ids (strings, hashable). Edges are rule-to-rule
  dependencies declared via `RuleRuleParent`.
- `field` and `validator` parents are **context**, not edges — they
  declare which fields / validators the rule reads, so the engine
  scopes the prompt down to only the relevant data.
- The engine walks the DAG with `graphlib.TopologicalSorter`. Every
  level is **one** LLM call that evaluates all rules whose parents are
  resolved.

See [rule-engine.md](rule-engine.md) for the full mechanics.

### 4i. `assemble`

Pure Python. A no-op node; the actual result composition happens in
`PipelineOrchestrator._build_result` after the engine completes. The
node is there so the trace shows a clean end-marker.

---

## 5. Concurrency model

Three places where concurrency matters:

1. **Per-doc fan-out inside a stage.** `extract`, `visual_authenticity`,
   `content_authenticity`, and `judge` run their per-doc work through
   `asyncio.gather`. One slow document blocks only its sibling tasks
   transiently — the stage as a whole finishes when the slowest one
   does.
2. **Per-stage timeouts.** Each `CallableStep` has a `timeout_seconds`.
   The pipeline engine cancels the stage when it exceeds; the
   orchestrator records the failure and moves on.
3. **Sync request ceiling.** The `ExtractCommand` is wrapped in
   `asyncio.wait_for(SYNC_TIMEOUT_S)` by `ExtractHandler` — the
   request returns 408 if the pipeline takes longer.

For the async API (`POST /api/v1/jobs`), the ceiling is
`FLYDESK_IDP_ASYNC_TIMEOUT_S` (default 300 s); failed attempts are
re-queued up to `FLYDESK_IDP_JOB_MAX_ATTEMPTS`.

---

## 6. Failure isolation

A stage that raises does **not** abort the request. The orchestrator
catches the exception, appends a structured entry to
`pipeline_errors[]`, and continues:

```json
{
  "node": "judge",
  "code": "JUDGE_ERROR",
  "message": "anthropic timed out after 180s"
}
```

The downstream stages (`rules`, `assemble`) still run with whatever
state is available. This is what lets the service return partial
results — the field extraction succeeded, only the judge bailed.

---

## 7. Adding a new stage

Six steps:

1. **Add the service** under `core/services/<stage>/` as a regular
   class. Constructor takes its `PromptTemplate` (if it calls an LLM)
   plus any other deps — see how `Judge` is laid out.
2. **Bundle the prompt** as `resources/prompts/<stage>.yaml` (see
   [prompts.md](prompts.md)) — keep it in the same versioning scheme.
3. **Register it in `PromptCatalog`** by adding an entry to
   `_PROMPT_FILES` plus a named accessor.
4. **Declare the bean** in `IDPCoreConfiguration` with the template
   resolved from the catalog.
5. **Inject it into `PipelineOrchestrator`** and add an
   `async def _step_<stage>(...)` method that reads/writes
   `ctx.metadata`.
6. **Wire the toggle** — extend `StageToggles` in
   `interfaces/dtos/extract.py` and add the
   `builder.add_node(..., timeout_seconds=...)` block in `execute`.

If the stage takes the document bytes (i.e. it's multimodal), also
make sure it respects the splitter's `per_doc_inputs` map so it sees
the right slice when a single source contains multiple target docs.

---

## 8. Debugging recipes

| Symptom                                                          | Where to look                                                                                                                          |
| ---------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Pipeline never reached a stage                                   | Check `stages` in the request — many stages are opt-in.                                                                                |
| Stage timed out at exactly its budget                             | The `timeout_seconds` triggered. Either bump the env var, switch to async, or drop the stage.                                          |
| Field located but `bbox` is all zeros                            | Extractor returned `value=null` (so no bbox), or the LLM emitted a degenerate box and the post-processor zeroed it. Check `notes`.    |
| `judge.status == "FAIL"` on a correct value                      | The judge is strict by design. Read `judge.evidence` and `judge.notes` to understand why — usually a layout the LLM couldn't anchor.   |
| Validator says NIF but the value is a NIE                        | Declare both validators with `severity: warning` (see [troubleshooting.md](troubleshooting.md)).                                       |
| Rule output is outside `valid_outputs`                            | Engine flags the rule. Inspect `human_revision` for the LLM's reason and route to manual review.                                       |
| `pipeline_errors` non-empty but request succeeded                | Expected — partial success. Caller decides whether to accept or re-submit with the failed stage disabled.                              |
