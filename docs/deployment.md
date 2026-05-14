# Deployment

Notes for taking flydesk-idp from a developer laptop to a real
environment — topology, configuration, scaling, security, and cost.

---

## 1. Topology

A production deployment has four moving parts:

```
                ┌──────────────┐
   client ─────▶│ API (uvicorn)│──── publish ────▶ ┌──────────────┐
                │ /api/v1/...  │                   │ Redis Streams│
                │ /actuator/...│                   └──────┬───────┘
                └──────┬───────┘                          │ consume
                       │                                   ▼
                       │                          ┌──────────────┐
                       │ persists                 │ JobWorker(s) │
                       ▼                          └──────┬───────┘
                ┌──────────────┐                          │
                │ Postgres     │ ◀────── reads/writes ────┘
                └──────────────┘
```

| Component   | Role                                                                                          |
| ----------- | --------------------------------------------------------------------------------------------- |
| **API**     | One or more uvicorn workers behind a load balancer. Stateless; sticky sessions not required.   |
| **Worker**  | One or more processes that consume the job stream. Each Redis consumer-group message goes to one worker. |
| **Postgres**| Holds `extraction_jobs`. Idempotency, status, and results live here.                          |
| **Redis**   | The job queue. Streams persist messages so a worker restart never loses a job.                |

The API and the worker share the same wheel; the difference is which
CLI subcommand starts them (`flydesk-idp serve` vs.
`flydesk-idp worker`).

---

## 2. Environment

The full list lives in [`env_template`](../env_template). The hot
ones:

```env
FLYDESK_IDP_PORT=8400
FLYDESK_IDP_LOG_LEVEL=INFO

FLYDESK_IDP_DATABASE_URL=postgresql+asyncpg://idp:s3cret@db:5432/flydesk_idp

# In-memory is fine for single-process dev; use Redis in production.
FLYDESK_IDP_EDA_ADAPTER=redis
FLYDESK_IDP_REDIS_URL=redis://redis:6379/0
FLYDESK_IDP_JOBS_TOPIC=flydesk.idp.jobs

# Models — Anthropic first, OpenAI as a fallback when the primary errors out.
FLYDESK_IDP_MODEL=anthropic:claude-sonnet-4-6
FLYDESK_IDP_FALLBACK_MODEL=openai:gpt-4o

# Timeouts (seconds).
FLYDESK_IDP_SYNC_TIMEOUT_S=60
FLYDESK_IDP_ASYNC_TIMEOUT_S=300
FLYDESK_IDP_JOB_MAX_ATTEMPTS=3

# Document size / page caps.
FLYDESK_IDP_MAX_BYTES=33554432       # 32 MiB
FLYDESK_IDP_MAX_SYNC_PAGES=10

# Webhook delivery.
FLYDESK_IDP_WEBHOOK_TIMEOUT_S=15
FLYDESK_IDP_WEBHOOK_MAX_ATTEMPTS=5
FLYDESK_IDP_WEBHOOK_HMAC_SECRET=<a-strong-random-string>

# Optional API-key auth (comma-separated).
FLYDESK_IDP_API_KEYS=tenant-a-secret,tenant-b-secret

# Provider credentials (standard names; not prefixed).
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
```

---

## 3. Building the image

```bash
task docker:build
```

Or directly:

```bash
docker buildx build \
    --build-context pyfly=../../fireflyframework/fireflyframework-pyfly \
    --build-context fireflyframework-agentic=../../fireflyframework/fireflyframework-agentic \
    --tag flydesk-idp:0.1.0 \
    .
```

The two `--build-context` references stage the sibling Firefly
libraries as named contexts; the `Dockerfile` rewrites the
`pyproject.toml` source paths so `uv sync` resolves them inside the
container.

---

## 4. Running migrations

Migrations are Alembic-based. Three ways to apply them:

1. **From the host**: `task dev:migrate` (requires
   `FLYDESK_IDP_DATABASE_URL` set in your shell).
2. **From the image**: `docker run --rm ... flydesk-idp:0.1.0 migrate`.
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

The composite includes:

- DB connectivity (`pyfly.data.relational` health indicator).
- Redis connectivity (when `FLYDESK_IDP_EDA_ADAPTER=redis`).
- A custom indicator can be added by registering a
  `pyfly.actuator.health.HealthIndicator` bean.

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
2. Find the slow `request_id` in the metrics' high-cardinality labels
   or the access log.
3. Grep the JSON logs for that `request_id`. Every pipeline node
   stamps its `node_start` / `node_done` / `node_failed` line.
4. Cross-reference with the trace span tree to find the slowest stage.

---

## 7. Scaling

| Component  | Strategy                                                                                                                                                                |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **API**    | Stateless — scale horizontally. Set uvicorn workers via `--workers` (one per CPU is a good default). Sticky sessions are not required.                                  |
| **Worker** | Stateless — scale horizontally. Redis consumer groups shard message delivery. Right-size against peak job arrival; one worker handles ~1 job/min if each takes ~30 s.    |
| **Postgres** | A single primary is fine for the `extraction_jobs` table size you'll see. Add a read replica only if `/api/v1/jobs/{id}` reads dominate and you want to offload them. |
| **Redis**  | Single primary is fine; the stream durability covers worker restarts. Use a managed offering or a small Sentinel setup for HA.                                          |

---

## 8. Security

| Concern          | Posture                                                                                                                                                       |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Document bytes** | Never written to disk on the service side. Only the base64 payload sits in `extraction_jobs.schema_json` for the job lifetime. Replace with a blob-store pointer if your DB can't keep this. |
| **Webhook HMAC** | Mandatory in production — set `FLYDESK_IDP_WEBHOOK_HMAC_SECRET` to a strong random string. The publisher signs every payload with HMAC-SHA256.                  |
| **API keys**     | Entry-level gate — set `FLYDESK_IDP_API_KEYS` to a comma-separated list. For OIDC / OAuth2, swap in pyfly's `security-jwt` starter and add a JWT decoder bean. |
| **LLM keys**     | Provider credentials are read from standard env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`). Use your secrets manager; never bake them into the image.        |
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
2. **Model choice.** `FLYDESK_IDP_MODEL` is the default; override per
   request via `options.model`. Lighter models (haiku, gpt-4o-mini)
   are fine for high-volume extraction; reserve the heavier ones for
   adversarial or low-confidence cases.
3. **Fallback.** `FLYDESK_IDP_FALLBACK_MODEL` is used when the primary
   errors out. Setting it to a cheaper model avoids double-paying for
   a single failed call — and gives you a graceful degradation path.

A useful pattern in production: run the cheap model by default and
**re-submit** with the expensive model when the judge flags too many
fields for human review. That's a one-line policy in the caller's
workflow, not a code change here.
