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
|     1 | `load`                 | yes        | Sniff media type and count pages on every input file. Pure Python.                                                                       | 20 s            |
|     2 | `discover`             | no         | LLM enumerates every sub-document inside each unpinned, multi-page file. Returns one segment per discovered sub-document with a page range. | 180 s        |
|     3 | `classify`             | no         | LLM assigns each segment a declared `DocSpec` (or the `unmatched` sentinel). Per-segment fan-out via `asyncio.gather`.                   | 180 s           |
|     4 | `plan_tasks`           | yes        | Pure Python: build the flat `(segment, DocSpec)` task list every downstream stage iterates.                                              | 5 s             |
|     5 | `extract`              | yes        | LLM produces fields + normalised bboxes for every task. Fanned out with `asyncio.gather`.                                                | 300 s           |
|     6 | `bbox_validation`      | yes        | Pure-Python geometric hallucination check: stamps every bbox with a `BboxQuality` verdict + continuous `quality_score`.                   | 5 s             |
|     7 | `field_validation`     | no         | Pure-Python validation: regex / enum / range + every `StandardValidator` declared per field.                                             | 5 s             |
|     8 | `visual_authenticity`  | no         | LLM evaluates caller-defined visual validators (signature present, stamp present, …).                                                    | 180 s           |
|     9 | `content_authenticity` | no         | LLM audit: dates consistent, totals add up, expected boilerplate, tampering signals.                                                     | 180 s           |
|    10 | `judge`                | no         | Second LLM pass re-grades every extracted value against the source.                                                                       | 180 s           |
|    11 | `judge_escalation`     | no         | When the judge's failure rate exceeds `escalation_threshold`, re-run extract + judge with `escalation_model` and keep the better result. | 300 s           |
|    12 | `rules`                | no         | LLM evaluates the business-rule DAG, level by level.                                                                                     | 180 s           |
|    13 | `assemble`             | yes        | Pure Python: compose the `ExtractionResult`.                                                                                              | 5 s             |

Optional stages are caller-toggled through `ExtractionOptions.stages`.
Other short-circuits:

- `discover` (the splitter) runs only when `stages.splitter` is on AND
  at least one file is unpinned AND that file has more than one page.
  Pinned files are treated as a single segment of the pinned type.
- `classify` runs per **segment**, not per file. Skipped for segments
  whose doctype is already resolved (pin or single-declared-DocSpec
  short-circuit), and as a whole when `stages.classifier` is off.
- `bbox_validation` is non-toggleable — it is pure geometry and runs
  on every request.
- `judge_escalation` is silently skipped when `judge` is off.

---

## 3. How the DAG is built

```python
builder = PipelineBuilder("flydesk-idp")
builder.add_node("load", CallableStep(self._step_load), timeout_seconds=20)

# Discover runs when the splitter is on AND at least one file is unpinned.
# Pinned files keep their single default segment (covers the whole file).
needs_discover = stages.splitter and any(not f.document_type for f in files)
if needs_discover:
    builder.add_node("discover", CallableStep(self._step_discover), timeout_seconds=180)

# Classify runs per segment when on. The step short-circuits internally
# when there are no segments needing a doctype (everything pinned, or
# only one declared DocSpec).
if stages.classifier:
    builder.add_node("classify", CallableStep(self._step_classifier), timeout_seconds=180)

builder.add_node("plan_tasks",     CallableStep(self._step_plan_tasks),     timeout_seconds=5)
builder.add_node("extract",        CallableStep(self._step_extract),        timeout_seconds=300)
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

> **Unified flow.** Every file -- whether the caller submitted
> `document` or `documents[]` -- flows through the same pipeline. The
> orchestrator normalises everything into two flat lists kept on the
> pipeline context:
>
> - `files_data: list[_FileSlot]` -- one slot per input file.
> - `tasks: list[_ExtractionTask]` -- one task per **(segment, DocSpec)**
>   pair, where a "segment" is a sub-document inside a file.
>
> A segment is produced by the `discover` stage (one segment per
> sub-document the LLM identifies) or, when discovery is off /
> short-circuited, a default segment that covers the whole file. Each
> segment is then assigned a declared DocSpec by the `classify` stage
> (or by a caller pin, or by the single-DocSpec short-circuit). A task
> is produced for every segment that ends up with a resolved DocSpec;
> the rest go to `result.additional_documents` as `unmatched`.

### 4a. `load`

`core/services/extraction/loader.py::load_document` runs once per input
file. It sniffs the media type (magic bytes first, declared content
type as a fallback) and counts pages (`pypdf` for PDF; everything else
is one page from the extractor's point of view). Each file becomes a
`_FileSlot` in `ctx.metadata["files_data"]` and is seeded with **one
default segment** covering the whole file. The discover stage may
later replace that with finer-grained segments. For pinned files the
default segment's `resolved_doctype` is set from the pin so they skip
classification.

No LLM calls. Cheap.

### 4b. `discover`

`core/services/splitting/splitter.py`. One LLM call per unpinned,
multi-page file, fanned out with `asyncio.gather`.

The splitter is a pure **segmentation** service: it enumerates every
distinct sub-document inside a file and returns one entry per
sub-document with a contiguous page range, a free-text
`provisional_type` hint, a description, and a segmentation
`confidence`. It does NOT decide which declared DocSpec each segment
matches -- that is the classifier's job.

The caller's declared DocSpecs are passed to the LLM as routing
context (so it can recognise familiar layouts), not as a constraint:
the splitter outputs what is actually in the file, even when no
declared target matches.

The stage is skipped when:

- `stages.splitter` is off, or
- the file is pinned with a `document_type` (caller already told us
  what it is), or
- the file has a single page (one segment is always enough).

When the LLM returns an empty list, the splitter falls back to a
single segment covering the whole file so the pipeline can still
proceed.

### 4c. `classify`

`core/services/classification/classifier.py`. One LLM call per
**segment**, fanned out with `asyncio.gather`.

Each call sees the segment bytes (the file is sliced down to the
segment's page range with `pypdf` for PDFs; for non-PDF inputs the
whole file is sent) plus the JSON dump of every candidate
`DocSpec.docType`. The LLM returns one of the declared `documentType`
values or the literal `"unmatched"`. Any value outside the closed
candidate set is coerced to `unmatched` defensively.

The per-segment verdict is surfaced on the corresponding
`documents[]` or `additional_documents[]` entry. For single-segment
files we additionally roll the verdict up onto
`result.files[i].classification` so the top-level summary is useful.

The stage is skipped when:

- `stages.classifier` is off, or
- the segment already has a resolved doctype (caller pin, or the
  single-declared-DocSpec short-circuit that auto-assigns the only
  candidate without an LLM call).

### 4d. `plan_tasks`

Pure-Python bookkeeping (no LLM). Walks the per-file segments and
produces one `_ExtractionTask` per (segment, DocSpec) pair where the
segment has a resolved doctype. PDF segments that don't cover the
whole file get sliced down with `pypdf` so the extractor sees only
the segment's pages. Unmatched segments are routed to
`additional_documents` and never reach the extractor.

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

> **Known limitation — near-future improvement.** LLM-estimated bboxes
> are imprecise: they land in roughly the right region of the page but
> routinely miss the actual text by one or more lines. The geometric
> validator cannot catch this. The planned fix is **text-layer
> grounding**: extract word-level coordinates with `pdfplumber` for
> born-digital PDFs and Tesseract OCR for scanned PDFs / images, then
> replace the LLM's box with the union of the words that match the
> extracted value. Once in place the response will distinguish the two
> via a `bbox.source: "llm" | "ocr"` discriminator. Until that ships,
> treat bboxes as a "where to look" hint, not a precise locator -- see
> the warning in `interfaces/dtos/bbox.py`.

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

## 7. Outbound call logging + cost telemetry

### 7a. Log line shape

Every call the service makes outside its own process emits a single
structured `outbound_call` log line via
`core/observability/outbound_log.py::log_outbound`. The format is
log-line-oriented (key=value), so a single grep surfaces the entire
external-call footprint with full per-call cost data:

```text
outbound_call target=anthropic op=split status=ok latency_ms=14418 model=anthropic:claude-opus-4-7 correlation_id=3d530f07-... in_tokens=48598 out_tokens=746 total_tokens=49344 cost_usd=0.784920
outbound_call target=anthropic op=extract status=ok latency_ms=21352 model=anthropic:claude-opus-4-7 correlation_id=3d530f07-... in_tokens=12500 out_tokens=850 total_tokens=13350 cost_usd=0.251250
outbound_call target=anthropic op=judge status=ok latency_ms=15162 model=anthropic:claude-opus-4-7 correlation_id=3d530f07-... in_tokens=11200 out_tokens=820 total_tokens=12020 cost_usd=0.229500
outbound_call target=webhook op=deliver status=ok latency_ms=12 url=https://... attempt=1 http_status=200 job_id=39e0...
outbound_call target=worker op=job.run status=ok latency_ms=42557 job_id=39e0... attempt=1
```

Targets currently emitted:

- `anthropic` / `openai` -- LLM provider, one line per stage call
  (`op=split`, `op=classifier`, `op=extract`, `op=visual_auth`,
  `op=content_auth`, `op=judge`, `op=rules.level.<n>`). Includes
  `correlation_id`, `in_tokens`, `out_tokens`, `total_tokens`, and the
  estimated `cost_usd` for each call.
- `webhook` -- one line per delivery attempt (`op=deliver`).
- `worker` -- one line per job start and per terminal outcome
  (`op=job.run`).
- `queue` -- one line per delayed re-publish during a retry.

Use these for spend tracing (sum `cost_usd` by model or per request),
SLO monitoring, and forensic analysis when a request behaves oddly.

### 7b. How cost surfaces in the response

The framework's `fireflyframework_agentic.observability.usage`
subsystem records a `UsageRecord` for every `FireflyAgent.run` call
(tokens, cost in USD, latency, model, agent name, `correlation_id`).
The orchestrator binds the request id to a `ContextVar` at the start
of `execute()`, and `timed_agent_run` (`core/observability/outbound_log.py`)
reads it and threads it into the agent's `AgentContext` so every record
the framework writes carries the right `correlation_id`. When the
engine returns its `PipelineResult`, `usage` is already aggregated for
that correlation id; the orchestrator maps it into the
`ExtractionResult.usage` field (`UsageBreakdown` -- `total_*`,
`by_agent`, `by_model`) and `PipelineResult.execution_trace` into
`ExtractionResult.trace`. See `docs/api-reference.md` for the exact
response shape.

### 7c. Pricing & prompt caching

The framework uses the `genai-prices` package as a live pricing source
for every model id we hand it. The full Claude 4 family
(`opus-4-*`, `sonnet-4-*`, `haiku-4-*`) is covered out of the box; the
USD figures in the response's `usage` block reflect Anthropic's
current published tariffs without any local override.

**Prompt caching.** Every `FireflyAgent` we construct ships with a
shared `PromptCacheMiddleware` from
`core/observability/agent_middleware.py::DEFAULT_MIDDLEWARE`. The
middleware injects pydantic-ai's `anthropic_cache_instructions` +
`anthropic_cache_messages` settings on every request, so the
Anthropic API caches the system prompt and the last user-message
block on the first call (5-minute TTL by default). Subsequent calls
within the TTL pay ~10% on cached tokens. The bbox / token line on
each `outbound_call` log line carries the per-call `cache_write` /
`cache_read` counts; the same data is aggregated into
`usage.cache_creation_tokens` / `usage.cache_read_tokens` on the
response.

> **Cache hits depend on prompt stability.** Anthropic caches by
> exact byte prefix of (instructions + tools + messages). Our system
> prompts are rendered through Jinja per call -- if the rendered text
> changes between calls (e.g. classifier with a different
> `targets_json`), the cache key changes and the next call writes a
> fresh cache instead of hitting the previous one. Cache writes show
> up immediately (`cache_write_tokens` > 0); cache reads
> (`cache_read_tokens` > 0) only appear when the same agent runs
> twice in a row with an identical rendered system prompt. Stabilising
> the templates -- moving per-call variables into the user message --
> is tracked as a follow-up.

**Disabling the cache.** Set `FLYDESK_IDP_PROMPT_CACHE=off` (or `0` /
`false` / `no`) in the service env to attach the middleware list
empty for the next process start. Useful for A/B benchmarking, for
quick rollback when caching misbehaves, and for low-volume workloads
where the per-call write premium (`+25%` over normal input) can
exceed the read discount (`-90%` of input) if cache hit-rate is low.

**Observed cost characteristics.** In our bastanteo benchmark
(1 request -> ~27 LLM calls, mostly-unique per-call prompts) the
warm-cache run costs ~33% more than the no-cache run for the *same
request*. The first ON-mode request is roughly 2x because it writes
the whole cache without reading anything. We believe this is the
expected Anthropic-side accounting for high-fanout, low-repetition
workloads -- cache pays off when the same prefix is replayed many
times within the 5-minute TTL, e.g. batch reprocessing of the same
expediente against multiple DocSpec variations. The toggle is there
so callers with that pattern can stay on while one-shot consumers
can flip it off.

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
