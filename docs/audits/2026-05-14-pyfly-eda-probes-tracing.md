# 2026-05-14 — Pyfly EDA, k8s probes, W3C tracing audit

**Repos in scope**

| Repo | Path | Role |
| --- | --- | --- |
| `fireflyframework-pyfly` | `../../fireflyframework/fireflyframework-pyfly` | Upstream framework (DI, CQRS, EDA, web, observability, actuator) |
| `flydocs` | this repo | Downstream service using pyfly |

This audit covers three intersecting concerns:

1. EDA adapter coverage in pyfly (Kafka, Postgres, Redis) and whether
   they are docker-tested.
2. Migrating the flydocs async-jobs path off the bespoke
   `JobQueue` onto a real pyfly EDA adapter — Postgres-backed by
   default.
3. K8s probes and W3C trace context: what's in pyfly today, what
   flydocs has bolted on locally, and what should move upstream.

---

## 1. Findings

### 1.1 EDA layer — `pyfly.eda`

| Component | State | Evidence |
| --- | --- | --- |
| `EventPublisher` port | **OK** | `pyfly/eda/ports/outbound.py` — `subscribe`, `publish`, `start`, `stop` |
| `EventEnvelope` type | **OK** | `pyfly/eda/types.py` — `event_type`, `payload`, `destination`, `event_id`, `timestamp`, `headers` |
| Decorators (`@event_listener`, `@event_publisher`, `@publish_result`) | **OK** | `pyfly/eda/decorators.py` |
| DLQ store | **OK** | `pyfly/eda/dlq.py` — `EdaDeadLetterStore` + `InMemoryEdaDeadLetterStore` |
| Circuit breaker | **OK** | `pyfly/eda/circuit_breaker.py` |
| Event filters | **OK** | `pyfly/eda/filter.py` — `HeaderEventFilter`, `PredicateEventFilter` |
| Serializers | **Partial** | JSON works (`JsonEventSerializer`). Avro/Protobuf raise `NotImplementedError` |
| **In-memory adapter** | **OK** | `pyfly/eda/adapters/memory.py` — `InMemoryEventBus` |
| **Kafka adapter** | **MISSING** | No `pyfly/eda/adapters/kafka.py`. (`pyfly.messaging` has a Kafka broker but it's the byte-level abstraction, not the event-level one.) |
| **Redis adapter** | **MISSING** | No `pyfly/eda/adapters/redis.py`. |
| **Postgres adapter** | **MISSING** | No `pyfly/eda/adapters/postgres.py`. |
| **EDA auto-configuration** | **MISSING** | No `pyfly/eda/auto_configuration.py`, no `pyfly.auto_configuration -> eda` entry point in `pyproject.toml` |
| Tests | **Partial** | `tests/eda/test_eda_enhancements.py` covers DLQ/circuit-breaker/serializers/filters but not the publish/subscribe path |
| Docker integration tests | **MISSING** | No testcontainers harness for any broker |

**Verdict.** The abstraction is solid and broker-agnostic. We just
don't have any production-grade adapter yet. The downstream service
(`flydocs`) routed around this by writing its own `JobQueue` —
which is exactly the duplication we want to eliminate.

### 1.2 K8s probes — `pyfly.actuator`

| Component | State | Evidence |
| --- | --- | --- |
| `/actuator/health` | **OK** | `pyfly/actuator/endpoints/health_endpoint.py` |
| `/actuator/health/liveness` | **OK** | `make_starlette_actuator_routes()` mounts it automatically |
| `/actuator/health/readiness` | **OK** | same |
| 503 on DOWN | **OK** | `get_status_code()` returns 503 when any indicator is DOWN |
| `ProbeGroup.LIVENESS / READINESS` | **OK** | `pyfly/actuator/health.py` |
| `HealthIndicator` protocol + aggregator | **OK** | aggregator collects failures and treats exceptions as DOWN |
| Auto-discovery of `HealthIndicator` beans | **Needs verification** | `ActuatorAutoConfiguration` registers the aggregator; we still need to verify it scans beans implementing `HealthIndicator` and `add_indicator()`s them automatically |
| Stock indicators for DB / Redis / Kafka / Postgres | **MISSING** | Each downstream service has to hand-roll its own |

**Verdict.** Pyfly already has Spring-Boot-equivalent probes. The
plumbing is correct and properly returns 503. What's missing are
**stock indicators** for the standard infra dependencies, so every
service has to write its own (and most, including flydocs,
don't). Add `DatabaseHealthIndicator`, `RedisHealthIndicator`,
`KafkaHealthIndicator`, `PostgresHealthIndicator` upstream and
auto-register them when both the underlying adapter and the
actuator are present.

### 1.3 W3C trace context — `pyfly.observability` + `pyfly.web` filters

| Component | State | Evidence |
| --- | --- | --- |
| `pyfly.observability.tracing.span` decorator (OTel) | **OK** | wraps OpenTelemetry, no-op when not installed |
| OpenTelemetry tracer provider bean | **OK** | `TracingAutoConfiguration` registers it when `opentelemetry` is installed |
| `TransactionIdFilter` (`X-Transaction-Id`) | **OK** | `pyfly/web/adapters/starlette/filters/transaction_id_filter.py` |
| `CorrelationContext` (`X-Correlation-ID`, `X-Trace-ID`, `X-Span-ID`) | **Partial** | `pyfly/cqrs/tracing/correlation.py` — exists but lives under CQRS, not at the observability/web layer |
| W3C `traceparent` / `tracestate` middleware | **MISSING** | Not parsed inbound, not propagated outbound. The only `traceparent` handling in either repo lives in `flydocs/web/correlation_filter.py` |
| `X-Tenant-Id` propagation | **MISSING** | Only flydocs has it |
| Log enrichment with correlation IDs | **MISSING** | Pyfly's logging adapters don't read the correlation context vars |

**Verdict.** Pyfly has fragments of the picture: transaction IDs
upstream, correlation IDs as a CQRS-internal concept, OpenTelemetry
span helpers. But the **W3C Trace Context surface (RFC 9110-friendly
`traceparent` + `tracestate`)** that flydocs needs at the HTTP
boundary is absent. flydocs filled the gap with its own
`CorrelationHeadersMiddleware`. That code is generic and belongs
upstream.

### 1.4 flydocs local code

| File | Status | Disposition |
| --- | --- | --- |
| `core/services/queue/job_queue.py` | Bespoke `JobQueue` (in-memory + Redis Streams) | **Delete** after migration — replace with pyfly EDA |
| `core/observability/correlation.py` | Duplicate ContextVar | **Delete** after promoting to pyfly |
| `web/correlation_filter.py` | Full W3C middleware | **Promote upstream**, then delete |
| `core/observability/outbound_log.py` | `log_outbound`, `measure`, `timed_agent_run` | Keep `timed_agent_run` (LLM-specific). Consider promoting `measure` + `log_outbound` later (out of scope here) |
| `core/observability/agent_middleware.py` | Anthropic prompt-cache middleware | Keep (IDP-specific) |
| No `HealthIndicator` beans | Health is blindly UP | **Add** DB + EDA indicators |

`IDPSettings.eda_adapter` already advertises `memory | redis | kafka | rabbitmq`
but the factory only ships `memory` and `redis`. We will extend it
to be a thin pass-through to the pyfly EDA autoconfiguration —
the `eda_adapter` value becomes `pyfly.eda.provider`.

---

## 2. Plan

### Phase A — Pyfly framework changes (upstream)

| # | Change | Files | Tests |
| - | --- | --- | --- |
| A1 | `KafkaEventBus` adapter | `pyfly/eda/adapters/kafka.py` | unit + `pytest.mark.integration` testcontainers (Kafka) |
| A2 | `RedisStreamsEventBus` adapter | `pyfly/eda/adapters/redis.py` | unit + testcontainers (Redis) |
| A3 | `PostgresEventBus` adapter (LISTEN/NOTIFY + outbox) | `pyfly/eda/adapters/postgres.py` | unit + testcontainers (Postgres) |
| A4 | EDA auto-configuration | `pyfly/eda/auto_configuration.py`, entry point | unit |
| A5 | `CorrelationFilter` (W3C `traceparent` / `tracestate` + `X-Correlation-Id` / `X-Request-Id` / `X-Tenant-Id`) | `pyfly/web/adapters/starlette/filters/correlation_filter.py`, register in `WebAutoConfiguration` | unit (Starlette `TestClient`) |
| A6 | `pyfly.observability.correlation` module (ContextVars + `current_correlation_context()`) | new module | unit |
| A7 | Stock `HealthIndicator` implementations | `pyfly/data/relational/health.py`, `pyfly/cache/health.py`, `pyfly/eda/health.py` per adapter | unit |
| A8 | Documentation in pyfly README + CHANGELOG entry | — | — |

### Phase B — flydocs migration (downstream)

| # | Change | Files |
| - | --- | --- |
| B1 | Swap `JobQueue` for `EventPublisher`. Publish `IDPJobSubmitted` events; subscribe in `JobWorker` via `@event_listener` | `core/configuration.py`, `core/services/workers/job_worker.py`, controllers that submit jobs |
| B2 | Default `FLYDOCS_EDA_ADAPTER=postgres` (was `redis`) | `docker-compose.yml`, `env_template`, `config.py` |
| B3 | Delete `core/services/queue/` | — |
| B4 | Re-point `set_correlation_id` callers at `pyfly.observability.correlation` | `core/services/pipeline/orchestrator.py`, `core/observability/outbound_log.py`, `core/observability/__init__.py` |
| B5 | Delete `core/observability/correlation.py` and `web/correlation_filter.py` | — |
| B6 | Stop registering `CorrelationHeadersMiddleware` in `main.py` — pyfly does it automatically now | `main.py` |
| B7 | Register `HealthIndicator` beans (DB + EDA) in `IDPCoreConfiguration` | `core/configuration.py` |

### Phase C — Verification (real, not stubbed)

| # | Step | Pass criterion |
| - | --- | --- |
| C1 | `pyfly` unit tests green (`uv run pytest` in `fireflyframework-pyfly`) | exit 0 |
| C2 | `pyfly` EDA integration tests via testcontainers (RUN_EDA_INTEGRATION=1) | round-trip + durability + reconnect pass for each broker |
| C3 | `task docker:up` in flydocs with `FLYDOCS_EDA_ADAPTER=postgres` | API + worker + postgres healthy |
| C4 | `curl POST /api/v1/jobs` with a sample PDF | 202, job row created in `extraction_jobs`, event row created in `pyfly_eda_outbox` |
| C5 | Worker logs show event consumed and webhook posted | observable in `docker compose logs worker` |
| C6 | `GET /actuator/health/readiness` | 200, components include `database` and `eda` both UP |
| C7 | Inbound `traceparent` header echoed back on the response | verified via curl |

### Phase D — Cleanup + commit

- Update `CLAUDE.md` to reflect the new EDA story.
- Update flydocs `docs/architecture.md` and
  `docs/deployment.md` to mention `pyfly.eda` and the Postgres-default
  posture.
- One commit per phase, conventional-commits style.

---

## 3. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Postgres EDA adapter uses LISTEN/NOTIFY which is best-effort under pgbouncer in transaction-pooling mode | Recommend `session` pooling or a dedicated direct connection for the consumer. Document it in adapter docstring + flydocs deployment doc. |
| Two outbox tables (per-service) if multiple downstreams adopt this | Use a configurable table name with sensible default (`pyfly_eda_outbox`). Single table per service is fine — events are not cross-service routed. |
| Kafka integration test slow/heavy | Mark `@pytest.mark.integration`, skip by default, run in CI nightly. |
| `CorrelationFilter` colliding with the existing `TransactionIdFilter` | They are independent (different headers); both run, `CorrelationFilter` sets `request.state.correlation_id` and `TransactionIdFilter` keeps `request.state.transaction_id`. |
| Pyfly auto-discovery missing the new `HealthIndicator` beans | Auto-register from each adapter's auto-configuration, not by bean scanning. Avoids depending on discovery behaviour. |

---

## 4. Out of scope

- Promoting `outbound_log.measure` / `log_outbound` upstream (Phase E,
  future audit).
- Avro / Protobuf serializer implementations (still stubs).
- AMQP / RabbitMQ EDA adapter (`pyfly.messaging` covers it at the
  byte level; if needed at event level, follow the same pattern as
  Kafka).
- Replacing pyfly's `cqrs/tracing/correlation.py` with the new
  observability one — they coexist; CQRS keeps its own internal
  trace state.
