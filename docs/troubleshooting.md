# Troubleshooting

Common failure modes and how to recover. If you hit something not
listed here, file an issue with the response `id` and the response
body (redacting any sensitive content).

> **What this doc covers:** the boot, request, async, validation,
> LLM, and stuck-extraction failure modes most teams hit early.
> **When to read it:** while debugging. **Where else to look:**
> - Stage internals: [`pipeline.md`](pipeline.md).
> - Concurrency model + lease windows: [`concurrency.md`](concurrency.md).

---

## Boot / DI

### `Failed to configure data with provider 'EngineLifecycle': password authentication failed`

`FLYDOCS_DATABASE_URL` doesn't match the running Postgres.

- Local dev: bring the database up with `task dev:db`, then run
  `task dev:migrate`.
- Docker compose: the `postgres` service uses `idp/idp/flydocs` by
  default. Override with `POSTGRES_USER` / `POSTGRES_PASSWORD` in
  `.env`.

### `No bean of type X found`

The bean is referenced by a constructor but the container can't
resolve it. Usually one of:

- The package containing the bean isn't in `scan_packages`. Add the
  parent package to `flydocs/app.py::scan_packages`.
- The class isn't decorated with `@service`, `@command_handler`,
  `@query_handler`, `@rest_controller`, or `@controller_advice`.
- The bean is a cross-cutting service that needs to be produced by
  `IDPCoreConfiguration` — add a `@bean` method there.

### `'list' object has no attribute 'document'`

A class with an `async def run(...)` method was treated as a
`fireflyframework-pyfly` `CommandLineRunner` and auto-invoked at
startup with `sys.argv[1:]`.

- Rename the method to something other than `run`. The
  `PipelineOrchestrator` uses `execute` for exactly this reason.

### `PromptValidationError: Template 'flydocs/X' is missing required variables: Y`

You edited a YAML prompt but forgot to update a call site (or vice
versa).

- Check `resources/prompts/<stage>.yaml` for `required_variables`.
- Compare with the call to `self._template.render(...)` in the
  corresponding service class.

---

## Requests / pipeline

### `408 timeout`

The sync pipeline didn't finish within `FLYDOCS_SYNC_TIMEOUT_S`
(default 60 s).

- Bump the timeout via the env var if 60 s is genuinely too short.
- Or switch the caller to the async API (`POST /api/v1/extractions`),
  which has a much longer ceiling (`FLYDOCS_ASYNC_TIMEOUT_S`, default
  300 s).
- Disable expensive stages (judge / content_authenticity / rules) if
  they aren't needed for this caller.

### `413 file_too_large`

Decoded file is over `FLYDOCS_MAX_BYTES` (default 32 MiB).

- Increase the limit if you trust the source.
- Or pre-resize / split the document client-side.

### `422 invalid_base64`

`content_base64` failed validation. Common causes:

- Wrapped in `data:application/pdf;base64,...` — supported, just make
  sure the prefix is intact.
- The encoding was URL-safe (`-`/`_` instead of `+`/`/`). flydocs
  accepts standard base64; transcode first if needed.
- Trailing whitespace / newlines — usually fine, but obviously corrupt
  payloads aren't.

### `pipeline.errors` populated but result returned

A stage failed but the pipeline kept going. The response carries
`status: "partial"` and:

```json
{"pipeline": {"errors": [{"node": "rule_engine",
                          "code": "stage_timeout",
                          "message": "openai timed out after 30s"}]}}
```

- Check the upstream provider's status page.
- Inspect the API logs for the matching `correlation_id` to see the
  exception trace.
- Re-submit with the failed stage disabled if you need a clean result
  quickly.

### Empty `field_groups` for one document

The `extract` stage failed for that specific doc. The response has
`pipeline.errors[].node == "extractor"` with the document type in the
message.

- Often a model-side issue (timeout, content policy). Try a fallback
  via `options.model` or set `FLYDOCS_FALLBACK_MODEL`.

---

## Async / queue

### Extraction stuck in `queued`

The worker process isn't running, or it isn't reading the right
queue.

- `docker compose ps` — is the `worker` container up?
- Check the worker logs for `ExtractionWorker … started (adapter=redis)`.
- Verify both API and worker see the same `FLYDOCS_EDA_ADAPTER`
  and `FLYDOCS_REDIS_URL`.

### Webhook never arrives

- Check `FLYDOCS_WEBHOOK_HMAC_SECRET` is set on both sides.
- The worker retries 5xx and 429 up to
  `FLYDOCS_WEBHOOK_MAX_ATTEMPTS` (default 5). A 4xx (other than
  429) is treated as permanent and logged.
- The webhook URL must be reachable from the worker network. In
  Docker compose, `http://host.docker.internal:...` is the canonical
  way to reach the host.

### `409 not_cancellable`

The extraction has already started. Cancellation is only allowed
while `status == "queued"`. To interrupt a running extraction, send
`SIGTERM` to the worker process; the orchestrator does not yet
support mid-flight cancellation.

### Extraction fails immediately with `permanent_error` instead of retrying

The worker classifies the exception as permanent (content-policy,
invalid API key, unsupported model, validator error from the request
body). Permanent errors skip the retry budget on purpose -- retrying
won't help. Inspect `error.message` for the provider's reason and fix
the input. Override by widening `_PERMANENT_ERROR_HINTS` only if you
have evidence the LLM provider's transient errors are landing in this
bucket by accident.

### Extraction retries too quickly / too slowly

The backoff is `min(retry_max_delay_s, retry_base_delay_s * 2^(attempt-1))`
plus 20% jitter. Tune via `FLYDOCS_RETRY_BASE_DELAY_S` and
`FLYDOCS_RETRY_MAX_DELAY_S` (seconds). Backoff applies only to
retryable errors -- permanent ones never re-queue.

### Escalation re-runs every request

`FLYDOCS_ESCALATION_THRESHOLD` is set too low (or 0.0 means
disabled, but anything > 0 starts evaluating it). Raise the threshold
or unset both threshold and `options.escalation.threshold` to disable.
Look for `judge_escalation triggered` log lines to see the failure
rate the orchestrator measured.

---

## Validation

### `Validator says <value> is not a Spanish NIE but it's a DNI`

Expected behaviour. The `nie` validator only accepts NIE-shaped
strings (`[XYZ]<7 digits><letter>`). DNIs are validated separately by
the `nif` validator. To accept either:

```jsonc
"validators": [
  {"name": "nif", "severity": "warning"},
  {"name": "nie", "severity": "warning"}
]
```

At least one of the two will fire as a warning when the other
matches; the field stays `valid` because warnings don't flip the flag.

### IBAN passes checksum but the bank rejects it

The validator only verifies ISO 13616 layout and the mod-97 checksum
— the IBAN may be syntactically valid but unassigned to any account.
Use a provider-side check for liveness.

---

## LLM / model

### `tool 'retries' is deprecated` deprecation warning

Source: `fireflyframework_agentic.agents.base`. Not actionable in
flydocs; the upstream library still uses the deprecated parameter.

### Model hallucinates a value with `confidence: 0.99`

The judge stage exists for this. Enable it
(`options.stages.judge: true`); the judge reads the same document and
flags values that aren't actually supported.

### `unhashable type: 'RuleSpec'`

Old bug, since fixed — but if it resurfaces it means the rule engine's
DAG nodes were changed back to `RuleSpec` objects. Nodes are strings
(rule ids); keep it that way.

---

## Stuck extractions (concurrency / orphan recovery)

The reaper sidecar in each worker container revives extractions whose
triggering event was lost. If an extraction appears stuck, walk this
short checklist first. See [concurrency.md](concurrency.md) for the
model.

### Extraction stuck in `running` for > 22 minutes

`async_timeout_s` (default 1200 s) caps any legitimate run; the lease
(default 1260 s = `async_timeout_s + 60`) adds a small grace period.
Past the lease, the reaper's next sweep republishes the event:

```sql
SELECT id, started_at, attempts, finished_at
FROM extractions
WHERE status='running'
  AND started_at < now() - INTERVAL '21 minutes';
```

If the reaper is running but the row stays `running`, check the worker
container logs for `ExtractionReaper republished extraction <id>`
lines. No republish over multiple sweep intervals means the reaper
isn't seeing the row -- usually `started_at` was bumped by a
heartbeat we don't yet implement, or the worker isn't actually
crashed. Operator override:

```sql
UPDATE extractions
SET started_at = now() - INTERVAL '24 hours'
WHERE id = '<extraction-id>' AND status='running';
```

The next reaper sweep will pick it up.

### Extraction stuck in `queued` for > 10 minutes

Either the submit handler crashed between row INSERT and outbox
PUBLISH, or a worker's retry-path `_delayed_publish` task died before
its `asyncio.sleep` fired. The reaper republishes after
`FLYDOCS_QUEUED_ORPHAN_THRESHOLD_S` (default 600 s).

Backlog vs. orphan: if many `queued` rows are old, your workers are
saturated, not crashed -- check throughput, not lifecycle. Add worker
replicas (`docker compose --scale worker=N` or the K8s equivalent;
multi-worker safe by design).

### Extraction stuck in `succeeded` with `post_processing.bbox_refinement.status='pending'`

Main extraction finished but the bbox-refine event wasn't published
(main worker crashed between `mark_succeeded` and the publish call).
`BboxReaper` revives it after
`FLYDOCS_POST_PROCESSING_PENDING_ORPHAN_THRESHOLD_S` (default 1320 s,
sized to `async_timeout_s + 120`). Until then the LLM-bbox result is
already readable via `GET /api/v1/extractions/{id}/result`.

### "Cannot cancel a running extraction"

Mid-flight cancellation is intentionally not supported. To stop a
runaway extraction:

1. Wait for the lease to expire (or trim `started_at` per the override
   above).
2. The reaper republishes; the row briefly transitions to `queued`
   when a worker requeues itself on the next failure, OR stays
   `running` if the redelivery wins again.
3. Issue `DELETE /api/v1/extractions/{id}` while it's `queued` to
   land in `cancelled`.

---

## Observability

### No metrics on `/actuator/metrics`

Make sure `pyfly.metrics.enabled: true` is in `fireflyframework-pyfly`'s
app config (it is by default via `@enable_core_stack`). Then scrape the
endpoint — output is Prometheus format.

### Logs lack correlation ids

The pipeline emits `correlation_id` automatically; the HTTP layer
adds `X-Correlation-Id` / `X-Request-Id` headers. If you're missing
them in upstream services (e.g. webhook receivers), pass them through
explicitly.
