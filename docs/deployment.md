# Deployment

Notes for taking flydocs from a developer laptop to a real
environment — topology, configuration, scaling, security, and cost.

> **What this doc covers:** topology, env vars, image variants,
> migrations, health probes, observability, scaling, security, cost
> tuning. **When to read it:** while shipping to staging or
> production. **Where else to look:**
> - Concurrency / multi-worker safety: [`concurrency.md`](concurrency.md).
> - CI/CD: [`cicd.md`](cicd.md).
> - Troubleshooting recipes: [`troubleshooting.md`](troubleshooting.md).

---

## 1. Topology

A production deployment has three moving parts:

```
                ┌──────────────┐                  ┌────────────────────────────┐
   client ─────▶│ API (uvicorn)│── INSERT + NOTIFY ▶│ Postgres                   │
                │ /api/v1/...  │                  │   extractions              │
                │ /actuator/...│                  │   pyfly_eda_outbox         │
                └──────────────┘                  │   pyfly_eda_offsets        │
                                                  └──────────┬─────────────────┘
                                                             │ LISTEN
                                                             ▼
                                                  ┌──────────────────────────────────┐
                                                  │ ExtractionWorker(s) (uvicorn)    │
                                                  │  fireflyframework-pyfly EDA      │
                                                  │  subscribe                       │
                                                  └──────────────────────────────────┘
```

| Component    | Role |
| ------------ | ---- |
| **API**      | One or more uvicorn workers behind a load balancer. Stateless; no sticky sessions. Publishes ``extraction.submitted`` events on the EDA bus. |
| **Worker**   | One or more processes that subscribe to the EDA bus via `fireflyframework-pyfly`'s `EventPublisher.subscribe`. Each event is delivered to exactly one consumer in the `flydocs-workers` consumer group. |
| **Postgres** | Holds `extractions` *and* the EDA outbox (`pyfly_eda_outbox` + `pyfly_eda_offsets`). With the Postgres EDA adapter no separate broker is required. |

Redis or Kafka are still supported brokers — see §3 — but the default
posture is **Postgres-only**: the service already runs Postgres for
persistence, so reusing it as a durable event bus removes one
operational dependency.

The API and the worker share the same image; the difference is which
CLI subcommand starts them (`flydocs serve` vs.
`flydocs worker`).

### Image variants

Two flavors are published on every release. Pick one per deployment.

| Tag prefix | Architectures | What's inside | When to pull |
| --- | --- | --- | --- |
| _(none)_ -- `latest`, `vX.Y.Z`, `main` | `linux/amd64` + `linux/arm64` | Slim runtime: PyMuPDF + Tesseract OCR, no PyTorch. The canonical artifact. | Default. Use unless you need layout-aware OCR or Markdown text-anchor. |
| `docling-` -- `docling-latest`, `docling-vX.Y.Z` | `linux/amd64` **only** | Slim image **plus** the `docling` extra: PyTorch + HF model loaders (~2.5 GB). Unlocks `FLYDOCS_BBOX_REFINE_OCR_ENGINE=docling` and `FLYDOCS_EXTRACTION_TEXT_ANCHOR=docling`. | When you want the Heron layout model grounding bboxes on noisy scans, or a Markdown anchor spliced into the extract prompt for multilingual / dense tabular documents. arm64 users build locally with `--build-arg WITH_DOCLING=true`. Details in [docling.md](docling.md). |

The `docling` variant is **not** distroless-friendly (writable
`~/.cache/docling`, `libstdc++` runtime). Stay on the slim image for
distroless deployments.

---

## 2. Environment

The full list lives in [`env_template`](../env_template). The hot
ones:

```env
FLYDOCS_PORT=8400
FLYDOCS_LOG_LEVEL=INFO

FLYDOCS_DATABASE_URL=postgresql+asyncpg://idp:s3cret@db:5432/flydocs

# EDA backend. ``postgres`` is the default and uses LISTEN/NOTIFY +
# a durable outbox in the same Postgres the service already owns.
# Other options: ``memory`` (single-process dev), ``redis`` (Redis
# Streams), ``kafka`` (aiokafka). The auto-configuration is in
# ``pyfly.eda.auto_configuration.EdaAutoConfiguration``;
# ``fireflyframework-pyfly``'s app config plumbs the env var into
# ``pyfly.eda.provider``.
FLYDOCS_EDA_ADAPTER=postgres
FLYDOCS_REDIS_URL=redis://redis:6379/0   # only used when adapter=redis
FLYDOCS_JOBS_TOPIC=flydocs.jobs

# Model selection. Pick any provider+model id that
# `fireflyframework-genai` / `fireflyframework-agentic` can resolve —
# `anthropic:…`, `openai:…`, `google:…`, `mistral:…`. The fallback
# model is used when the primary errors out; mix providers freely.
FLYDOCS_MODEL=anthropic:claude-sonnet-4-6
FLYDOCS_FALLBACK_MODEL=openai:gpt-4o

# Timeouts (seconds).
FLYDOCS_SYNC_TIMEOUT_S=60
FLYDOCS_ASYNC_TIMEOUT_S=300
FLYDOCS_EXTRACTION_MAX_ATTEMPTS=3

# Retry backoff bounds. The worker schedules each retry at
# min(retry_max_delay_s, retry_base_delay_s * 2^(attempt-1)) plus 20% jitter.
FLYDOCS_RETRY_BASE_DELAY_S=5
FLYDOCS_RETRY_MAX_DELAY_S=300

# Judge-driven escalation. When the judge fails more than this fraction
# of fields, the orchestrator re-runs extract + judge with the escalation
# model and keeps the better result. 0.0 disables (default).
FLYDOCS_ESCALATION_THRESHOLD=0.0
FLYDOCS_ESCALATION_MODEL=anthropic:claude-opus-4-7

# File size / page caps.
FLYDOCS_MAX_BYTES=33554432       # 32 MiB
FLYDOCS_MAX_SYNC_PAGES=10

# Webhook delivery.
FLYDOCS_WEBHOOK_TIMEOUT_S=15
FLYDOCS_WEBHOOK_MAX_ATTEMPTS=5
FLYDOCS_WEBHOOK_HMAC_SECRET=<a-strong-random-string>

# Optional API-key auth (comma-separated).
FLYDOCS_API_KEYS=tenant-a-secret,tenant-b-secret

# Provider credentials (standard names; not prefixed). Set whichever
# matches the model id you picked above — and the fallback too if it's
# a different provider. fireflyframework-genai reads these directly.
ANTHROPIC_API_KEY=...      # required for anthropic:* model ids
OPENAI_API_KEY=...         # required for openai:* model ids
# GOOGLE_API_KEY=...       # required for google:* model ids
# MISTRAL_API_KEY=...      # required for mistral:* model ids
```

---

## 3. Building the image

### 3.1 From the registry (recommended)

Production deploys should pull the prebuilt **multi-arch** image
published by `.github/workflows/docker-publish.yaml`:

```bash
docker pull ghcr.io/firefly-operationos/flydocs:latest      # arm64 + amd64 manifest
docker pull ghcr.io/firefly-operationos/flydocs:v26.05.01    # CalVer pin (YY.MM.PP)
docker pull --platform linux/arm64 ghcr.io/firefly-operationos/flydocs:latest
```

Available tag schemas:

| Source                  | Tags written                                                      |
| ----------------------- | ----------------------------------------------------------------- |
| `push` to `main`        | `main`, `sha-<short>`, `latest`                                   |
| `push` of `vYY.MM.PP` tag | `vYY.MM.PP`, `vYY.MM`, `vYY`, `sha-<short>`, `latest` (on main head) |
| `workflow_dispatch`     | `manual-<run_id>`                                                  |

Every tag carries a multi-arch manifest covering **linux/amd64** and
**linux/arm64**, plus SLSA build provenance and a CycloneDX SBOM
verifiable via `cosign verify-attestation`. See
[`docs/cicd.md`](cicd.md) for the workflow internals.

### 3.2 Local builds

```bash
task docker:build
```

Or directly:

```bash
docker buildx build \
    --build-context pyfly=../../fireflyframework/fireflyframework-pyfly \
    --build-context fireflyframework-agentic=../../fireflyframework/fireflyframework-agentic \
    --tag flydocs:26.05.01 \
    .
```

The two `--build-context` references stage the sibling Firefly
libraries as named contexts; the `Dockerfile` rewrites the
`pyproject.toml` source paths so `uv sync` resolves them inside the
container.

To build for a different arch (e.g. arm64 on an amd64 host) add
`--platform linux/arm64`. The CI workflow does this for both arches in
parallel via QEMU; on a workstation a single arch is usually enough.

---

## 4. Running migrations

Migrations are Alembic-based. Three ways to apply them:

1. **From the host**: `task dev:migrate` (requires
   `FLYDOCS_DATABASE_URL` set in your shell).
2. **From the image**: `docker run --rm ... flydocs:26.05.01 migrate`.
3. **At container start** (dev / staging): set `RUN_MIGRATIONS=true`
   on the `api` service; the entrypoint runs `alembic upgrade head`
   before serving.

Production deploys usually prefer option 2 — migrations are a deploy
step, not a request handler.

---

## 5. Health + readiness

`/actuator/health` returns the composite. Kubernetes probes:

```yaml
livenessProbe:
  httpGet: { path: /actuator/health/liveness, port: 8400 }
  initialDelaySeconds: 10
  periodSeconds: 30
readinessProbe:
  httpGet: { path: /actuator/health/readiness, port: 8400 }
  initialDelaySeconds: 5
  periodSeconds: 5
```

The composite always includes:

- **`database_health`** — `fireflyframework-pyfly`'s
  `pyfly.data.relational.health.SqlAlchemyHealthIndicator` pings the
  async engine with `SELECT 1` and surfaces the dialect on the
  response.
- **`eda_health`** — `fireflyframework-pyfly`'s
  `pyfly.eda.health.EventPublisherHealthIndicator` is auto-registered
  by `pyfly.eda.auto_configuration.EdaHealthAutoConfiguration` whenever
  the actuator subsystem is on. It reports the active adapter
  (`PostgresEventBus`, `RedisStreamsEventBus`, `KafkaEventBus`, or
  `InMemoryEventBus`).

A response from a healthy stack:

```json
{
  "status": "UP",
  "components": {
    "database_health": { "status": "UP", "details": { "database": "postgresql" } },
    "eda_health":      { "status": "UP", "details": { "adapter":  "PostgresEventBus" } }
  }
}
```

When any indicator is `DOWN`, the endpoint returns `503` so the
load-balancer / kubelet stops routing traffic. Add a service-specific
probe by registering another `pyfly.actuator.health.HealthIndicator`
bean (from `fireflyframework-pyfly`) in `core/configuration.py` — the
lifespan rescan picks it up automatically.

> **W3C trace context** is propagated by `fireflyframework-pyfly`'s
> default `CorrelationFilter`: every response echoes back `X-Correlation-Id`,
> `X-Request-Id`, `traceparent`, `tracestate`, and `X-Tenant-Id` when
> the request carried them. No middleware to wire locally.

---

## 6. Observability

| Telemetry      | Surface                                                                                              |
| -------------- | ---------------------------------------------------------------------------------------------------- |
| **Metrics**    | Prometheus at `GET /actuator/metrics` — CQRS handler latency, HTTP histograms, runtime metrics.       |
| **Traces**     | OpenTelemetry. Configure via standard env vars (`OTEL_EXPORTER_OTLP_ENDPOINT`, …). One span per pipeline node. |
| **Logs**       | structlog JSON. Every line carries `request_id`; correlation across API + worker is just a grep.       |
| **Health**     | `/actuator/health`, `/actuator/health/liveness`, `/actuator/health/readiness`, `/actuator/info`.        |

A typical investigation flow:

1. Alert fires on `extract_latency_p95_seconds > 60`.
2. Find the slow `correlation_id` in the metrics' high-cardinality labels
   or the access log.
3. Grep the JSON logs for that `correlation_id`. Every pipeline node
   stamps its `node_start` / `node_done` / `node_failed` line.
4. Cross-reference with the trace span tree to find the slowest stage.

Every call the service makes to an external system (LLM provider,
webhook receiver, queue broker, worker job lifecycle) emits a single
`outbound_call` line. Grep for `outbound_call target=<system>` to
trace external spend or correlate a slow request to its slowest
external call:

```text
outbound_call target=anthropic op=extract        status=ok  latency_ms=12879 model=anthropic:claude-opus-4-7
outbound_call target=anthropic op=judge          status=ok  latency_ms=15162 model=anthropic:claude-opus-4-7
outbound_call target=openai    op=extract        status=ok  latency_ms=11420 model=openai:gpt-4o
outbound_call target=webhook   op=deliver        status=ok  latency_ms=12    url=... attempt=1 http_status=200 correlation_id=...
outbound_call target=worker    op=extraction.run status=ok  latency_ms=42557 extraction_id=ext_... attempt=1
```

---

## 7. Scaling

| Component  | Strategy                                                                                                                                                                |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **API**    | Stateless — scale horizontally. Set uvicorn workers via `--workers` (one per CPU is a good default). Sticky sessions are not required.                                  |
| **Worker** | Stateless — scale horizontally. Multi-worker safety is enforced at three layers: a per-group `pg_try_advisory_lock` in the Postgres EDA adapter (or competitive consumer for Redis/Kafka), atomic `UPDATE … WHERE … RETURNING` state transitions on `extractions`, and a periodic reaper sidecar that republishes orphans whose triggering events were lost to a crash. See [concurrency.md](concurrency.md). Right-size against peak arrival; one worker handles ~1 extraction/min if each takes ~30 s. |
| **Bbox worker** | Same scaling story as the main worker, on a separate consumer group (`flydocs-bbox-workers`). Its `BboxReaper` sidecar revives stale `post_processing.bbox_refinement.status ∈ {pending, running}` orphans the same way. |
| **Postgres** | A single primary is fine for the `extractions` table size you'll see. Add a read replica only if `/api/v1/extractions/{id}` reads dominate and you want to offload them. The EDA outbox grows monotonically — schedule a periodic VACUUM of `pyfly_eda_outbox` if you don't already. |
| **Redis**  | Only required when `FLYDOCS_EDA_ADAPTER=redis`. Single primary is fine; the stream durability covers worker restarts. Postgres adapter (default) uses the database itself for the outbox — no Redis dependency. |

---

## 8. Security

| Concern          | Posture                                                                                                                                                       |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Document bytes** | Never written to disk on the service side. Only the base64 payload sits in `extractions.schema_json` for the extraction lifetime. Replace with a blob-store pointer if your DB can't keep this. |
| **Webhook HMAC** | Mandatory in production — set `FLYDOCS_WEBHOOK_HMAC_SECRET` to a strong random string. The publisher signs every payload with HMAC-SHA256.                  |
| **API keys**     | Entry-level gate — set `FLYDOCS_API_KEYS` to a comma-separated list. For OIDC / OAuth2, swap in `fireflyframework-pyfly`'s `security-jwt` starter and add a JWT decoder bean. |
| **LLM keys**     | Provider credentials are read from each provider's standard env var (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `MISTRAL_API_KEY`, …). `fireflyframework-genai` resolves the right one from the model id prefix. Use your secrets manager; never bake them into the image. |
| **TLS**          | Terminate at the load balancer. The service serves plain HTTP inside the cluster.                                                                              |

---

## 9. Cost tuning

The most expensive thing in the request is the LLM call. Three knobs
to lean on:

1. **Stage toggles.** The cheapest call enables only the extractor.
   Each extra stage = one or more LLM calls (per-doc fan-out and
   per-level rule eval multiply this further). Disable judges and
   content_authenticity for high-volume bulk runs; reserve them for
   high-risk paths.
2. **Model choice.** `FLYDOCS_MODEL` is the default; override per
   request via `options.model`. Lighter models (haiku, gpt-4o-mini)
   are fine for high-volume extraction; reserve the heavier ones for
   adversarial or low-confidence cases.
3. **Fallback.** `FLYDOCS_FALLBACK_MODEL` is used when the primary
   errors out. Setting it to a cheaper model avoids double-paying for
   a single failed call — and gives you a graceful degradation path.

A useful pattern in production: run the cheap model by default and
**re-submit** with the expensive model when the judge flags too many
fields for human review. That's a one-line policy in the caller's
workflow, not a code change here.
