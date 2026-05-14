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

| Order | Stage                  | Mandatory? | What it does                                                                                                                             | Default timeout |
| ----: | ---------------------- | :--------: | ---------------------------------------------------------------------------------------------------------------------------------------- | --------------: |
|     1 | `load`                 | yes        | Sniff media type and count pages on every input file. Pure Python.                                                                      | 20 s            |
|     2 | `classifier`           | no         | LLM picks which declared `DocSpec` each **unpinned** file matches. Skipped in single-file mode and when every file is pinned.            | 120 s           |
|     3 | `split`                | no         | LLM identifies the page range of every target `docType`. Single-file only — multi-file requests treat each file as its own document.    | 60 s            |
|     4 | `plan_tasks`           | yes        | Pure Python: build the flat `(file, DocSpec)` task list every downstream stage iterates.                                                | 5 s             |
|     5 | `extract`              | yes        | LLM produces fields + normalised bboxes for every task. Fanned out with `asyncio.gather`.                                               | 300 s           |
|     6 | `bbox_validation`      | yes        | Pure-Python geometric hallucination check: stamps every bbox with a `BboxQuality` verdict + continuous `quality_score`.                  | 5 s             |
|     7 | `field_validation`     | no         | Pure-Python validation: regex / enum / range + every `StandardValidator` declared per field.                                            | 5 s             |
|     8 | `visual_authenticity`  | no         | LLM evaluates caller-defined visual validators (signature present, stamp present, …).                                                   | 180 s           |
|     9 | `content_authenticity` | no         | LLM audit: dates consistent, totals add up, expected boilerplate, tampering signals.                                                   | 180 s           |
|    10 | `judge`                | no         | Second LLM pass re-grades every extracted value against the source.                                                                     | 180 s           |
|    11 | `judge_escalation`     | no         | When the judge's failure rate exceeds `escalation_threshold`, re-run extract + judge with `escalation_model` and keep the better result. | 300 s           |
|    12 | `rules`                | no         | LLM evaluates the business-rule DAG, level by level.                                                                                    | 180 s           |
|    13 | `assemble`             | yes        | Pure Python: compose the `ExtractionResult`.                                                                                            | 5 s             |

Optional stages are caller-toggled through `ExtractionOptions.stages`.
Other short-circuits:

- `classifier` runs only in multi-file mode AND when at least one file
  lacks a `document_type` pin AND `stages.classifier` is on (default
  `true`).
- `split` is single-file only and is additionally skipped when there's
  only one document type or the source is a single page.
- `bbox_validation` is non-toggleable — it is pure geometry and runs
  on every request.
- `judge_escalation` is silently skipped when `judge` is off.

---

## 3. How the DAG is built

```python
builder = PipelineBuilder("flydesk-idp")
builder.add_node("load", CallableStep(self._step_load), timeout_seconds=20)

# Classifier only runs in multi-file mode AND when some file lacks a pin.
needs_classifier = (
    stages.classifier and is_multi_file and any(not f.document_type for f in files)
)
if needs_classifier:
    builder.add_node("classifier", CallableStep(self._step_classifier), timeout_seconds=120)

# Splitter is single-file only.
if stages.splitter and not is_multi_file and len(request.docs) > 1:
    builder.add_node("split", CallableStep(self._step_split), timeout_seconds=60)

builder.add_node("plan_tasks", CallableStep(self._step_plan_tasks), timeout_seconds=5)
builder.add_node("extract",    CallableStep(self._step_extract),    timeout_seconds=300)
builder.add_node("bbox_validation", CallableStep(self._step_bbox_validation), timeout_seconds=5)

if stages.field_validation:
    builder.add_node("field_validation", ...)
# ...visual_authenticity, content_authenticity, judge, judge_escalation, rules...

builder.add_node("assemble", CallableStep(self._step_assemble), timeout_seconds=5)
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

> **Multi-file primer.** Two request shapes converge onto the same
> downstream pipeline. The orchestrator normalises both into a flat
> `tasks: list[_ExtractionTask]` -- one entry per `(file, DocSpec)`
> pair -- and every per-stage method iterates that list.
>
> - **Single file** (`request.document`, legacy shape): one task per
>   target `DocSpec` when the splitter ran, otherwise one task against
>   the whole document.
> - **Multi-file** (`request.documents = [...]`): one task per submitted
>   file. The caller may pin each file's `document_type`; unpinned
>   files go through the classifier. Files the classifier marks
>   `unmatched` skip extraction entirely and land in
>   `result.additional_documents` with `document_type="unmatched"`.

### 4a. `load`

`core/services/extraction/loader.py::load_document` runs once per input
file. It sniffs the media type (magic bytes first, declared content
type as a fallback) and counts pages (`pypdf` for PDF; everything else
is one page from the extractor's point of view). Each file becomes a
`_FileSlot` in `ctx.metadata["files_data"]`.

No LLM calls. Cheap.

### 4b. `classifier`

`core/services/classification/classifier.py`. One LLM call per
**unpinned** file, fanned out with `asyncio.gather`.

Each call sees the file bytes (multimodal `BinaryContent`) plus the
JSON dump of every candidate `DocSpec.docType` (id, description,
country). The LLM returns one of the declared `documentType` values or
the literal `"unmatched"`. The classifier coerces anything outside the
closed candidate set to `unmatched` defensively.

The per-file verdict is surfaced on `result.files[i].classification`
(matched, confidence, description, notes). Files that come back
`unmatched` skip extraction and appear in
`result.additional_documents` as `document_type="unmatched"`.

The stage is skipped when:

- the request is single-file (the docType is implicit in `docs[]`), or
- every file is pinned with a `document_type` (nothing to classify), or
- `stages.classifier` is off.

### 4c. `split`

> Single-file only. In multi-file mode each input is already one
> document — the splitter is short-circuited and `plan_tasks` builds
> one task per file directly.

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

### 4d. `plan_tasks`

Pure-Python bookkeeping (no LLM). Walks the per-file slots and the
splitter / classifier outcomes and produces the flat
`tasks: list[_ExtractionTask]` that every downstream stage iterates.
A task is one `(file, DocSpec, slice_bytes, page_range)` tuple --
exactly the unit `extract` runs once. The conversion from "request
shape" to "tasks" lives here, so the downstream stages don't have to
care whether the request was single- or multi-file.

### 4e. `extract`

`core/services/extraction/extractor.py`. One LLM call per task, fanned
out with `asyncio.gather`.

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
extractor retries on the fallback. The actual model used per task is
reflected in `result.model` (when more than one model contributed,
they're joined with a comma).

### 4f. `bbox_validation`

`core/services/bbox/bbox_validator.py`. Pure Python — no LLM, no OCR.

Runs on **every** request immediately after extract. For each
extracted field's `bbox`, the validator stamps:

- `quality`: one of `good`, `poor`, `suspicious`, `invalid`, `empty`,
- `quality_score`: continuous score in `[0, 1]` (area + aspect-ratio
  + margin-hugging components, weighted 0.5 / 0.3 / 0.2).

Heuristics, in priority order:

| Verdict       | Trigger                                                                                                       | Default score |
| ------------- | ------------------------------------------------------------------------------------------------------------- | ------------- |
| `empty`       | `fieldValueFound is None` or zero-area placeholder `BoundingBox.empty()`.                                     | 0.0           |
| `invalid`     | Corners outside `[0, 1]`, `xmin >= xmax`, or `ymin >= ymax` (pydantic also rejects most of these at parse).   | 0.0           |
| `suspicious`  | Area > 0.7 of the page, or covers > 0.9 horizontally / vertically. Classic LLM hallucination of a generic region. | 0.2           |
| `poor`        | Area < 5e-5 (~5px×5px on a 1000px-wide render) or extreme aspect ratio (height/width > 30 or < 1/30).        | 0.4           |
| `good`        | Anything else. Score combines area, aspect, and margin sanity.                                                | 0.5 – 1.0     |

This is a cheap geometric defence -- not a full OCR-grounded check. A
"good" verdict means the box is geometrically plausible; it does not
prove the LLM correctly anchored the value. Pair it with `judge` for
semantic anchoring.

### 4g. `field_validation`

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

### 4h. `visual_authenticity`

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

### 4i. `content_authenticity`

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

### 4j. `judge`

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

### 4k. `judge_escalation`

`core/services/escalation/judge_escalator.py`. Implements the
"cheap-by-default + escalate-on-uncertainty" policy: when the judge
flags too many fields as `FAIL` or `flag_for_review`, the orchestrator
re-runs the extractor and the judge with a stronger model and keeps
whichever result has the lower failure rate.

- The trigger threshold is `options.escalation_threshold` (per-request)
  or `FLYDESK_IDP_ESCALATION_THRESHOLD` (default `0.0` = disabled).
- The escalation model is `options.escalation_model` or
  `FLYDESK_IDP_ESCALATION_MODEL`. Same model as the primary disables
  the stage (no point re-running with the same engine).
- The new run is per-doc fan-out via `asyncio.gather` — same shape as
  `extract`, so latency stays bounded.
- The accepted/rejected outcome lands on `result.escalation`, an audit
  block with `primary_model`, `escalation_model`, `primary_fail_rate`,
  `escalation_fail_rate`, and `accepted` (true if the new fields
  replaced the originals).

Pattern in production: primary `claude-haiku` / `gpt-4o-mini`,
escalation `claude-opus` / `gpt-4o`. The cheap model handles ~80% of
traffic; the expensive one only kicks in on documents where the cheap
model misled the judge.

### 4l. `rules`

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

### 4m. `assemble`

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

## 7. Outbound call logging

Every call the service makes outside its own process emits a single
structured `outbound_call` log line via
`core/observability/outbound_log.py::log_outbound`. The format is
log-line-oriented (key=value), so a single grep surfaces the entire
external-call footprint:

```text
outbound_call target=anthropic op=extract status=ok latency_ms=12879 model=anthropic:claude-opus-4-7
outbound_call target=anthropic op=visual_auth status=ok latency_ms=7433 model=anthropic:claude-opus-4-7
outbound_call target=anthropic op=judge status=ok latency_ms=15162 model=anthropic:claude-opus-4-7
outbound_call target=anthropic op=rules.level.1 status=ok latency_ms=10666 model=anthropic:claude-opus-4-7
outbound_call target=webhook op=deliver status=ok latency_ms=12 url=https://... attempt=1 http_status=200 job_id=39e0... correlation_id=e2e-corr-001
outbound_call target=worker op=job.run status=ok latency_ms=42557 job_id=39e0... attempt=1
```

Targets currently emitted:

- `anthropic` / `openai` -- LLM provider, one line per stage call
  (`op=extract`, `op=split`, `op=visual_auth`, `op=content_auth`,
  `op=judge`, `op=rules.level.<n>`).
- `webhook` -- one line per delivery attempt (`op=deliver`).
- `worker` -- one line per job start and per terminal outcome
  (`op=job.run`).
- `queue` -- one line per delayed re-publish during a retry.

Use these for spend tracing (sum `latency_ms` by model), SLO
monitoring, and forensic analysis when a request behaves oddly.

---

## 8. Retry policy (async)

Async jobs that fail get classified into one of two buckets in
`core/services/workers/job_worker.py`:

| Classification | Examples                                                                 | Worker action |
| -------------- | ------------------------------------------------------------------------ | ------------- |
| **permanent**  | `ValueError` from the validator, content-policy / moderation rejections, invalid API key, unsupported model | `mark_failed(code=PERMANENT_ERROR)` immediately. Webhook fires with the failure detail. |
| **retryable**  | Timeouts, network errors, transient 5xx from the LLM provider, generic `RuntimeError` | Re-queue with exponential backoff: `min(retry_max_delay_s, retry_base_delay_s * 2^(attempt-1))` plus 20% jitter. |

The `attempts` counter is persisted atomically by
`ExtractionJobRepository.mark_running`. When `attempts ==
FLYDESK_IDP_JOB_MAX_ATTEMPTS`, the job goes to `FAILED` even if the
last error was retryable.

A delayed re-publish runs as a background `asyncio.create_task` so the
worker keeps draining the stream while the failed message waits its
backoff window. The re-publish is itself logged as
`outbound_call target=queue op=republish`.

---

## 9. Adding a new stage

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

## 10. Debugging recipes

| Symptom                                                          | Where to look                                                                                                                          |
| ---------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Pipeline never reached a stage                                   | Check `stages` in the request — many stages are opt-in.                                                                                |
| Stage timed out at exactly its budget                             | The `timeout_seconds` triggered. Either bump the env var, switch to async, or drop the stage.                                          |
| Field located but `bbox` is all zeros                            | Extractor returned `value=null` (so no bbox), or the LLM emitted a degenerate box and the post-processor zeroed it. Check `notes`.    |
| `judge.status == "FAIL"` on a correct value                      | The judge is strict by design. Read `judge.evidence` and `judge.notes` to understand why — usually a layout the LLM couldn't anchor.   |
| Validator says NIF but the value is a NIE                        | Declare both validators with `severity: warning` (see [troubleshooting.md](troubleshooting.md)).                                       |
| Rule output is outside `valid_outputs`                            | Engine flags the rule. Inspect `human_revision` for the LLM's reason and route to manual review.                                       |
| `pipeline_errors` non-empty but request succeeded                | Expected — partial success. Caller decides whether to accept or re-submit with the failed stage disabled.                              |
