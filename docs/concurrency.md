# Concurrency model

How flydocs handles concurrent operations safely when you scale workers
horizontally. Everything below is the *production* story — the docs
that describe individual stages still hold.

If you only run one `worker` and one `bbox-worker` container, you can
skip this page. If you ever plan to run more than one of either, read
it once.

---

## Where the work lives

The async path is split across three processes:

| Process              | Owns                                                                                  |
| -------------------- | ------------------------------------------------------------------------------------- |
| `flydocs serve`      | HTTP API. Persists the job row + publishes `IDPJobSubmitted` on the EDA bus.          |
| `flydocs worker`     | Main extraction. Subscribes to `IDPJobSubmitted`. Bundles a `JobReaper` sidecar.       |
| `flydocs bbox-worker`| Out-of-band bbox grounding. Subscribes to `IDPBboxRefineRequested`. Bundles a `BboxReaper` sidecar. |

Each worker container runs **two cooperating async tasks**: the
consumer loop and the reaper. If either crashes, the container exits
and the orchestrator restarts it — both are reset together.

---

## The four invariants

The whole concurrency story rests on four guarantees, in order from
inside-out:

### 1. Atomic state transitions (`extraction_jobs`)

Every state-changing repository method is a single conditional
`UPDATE ... WHERE id=? AND status IN (legal_predecessors) RETURNING *`.
Two writers racing on the same row are serialised by Postgres'
row-level UPDATE lock; the `WHERE` precondition picks exactly one
winner. The loser gets `None` back — never a partial write, never a
silently-clobbered field.

The legal-predecessor matrix:

| Method                        | Predecessors                                                            |
| ----------------------------- | ----------------------------------------------------------------------- |
| `mark_running`                | `QUEUED` OR (`RUNNING` with stale `started_at` past `job_run_lease_s`)  |
| `mark_succeeded`              | `RUNNING` OR `REFINING_BBOXES`                                           |
| `mark_failed`                 | `RUNNING`                                                                |
| `mark_partial_succeeded`      | `RUNNING`                                                                |
| `mark_bbox_refining`          | `PARTIAL_SUCCEEDED` OR (`REFINING_BBOXES` with stale `bbox_refine_started_at`) |
| `mark_bbox_refined`           | `REFINING_BBOXES`                                                        |
| `mark_bbox_refine_failed`     | `REFINING_BBOXES`                                                        |
| `mark_cancelled`              | `QUEUED`                                                                 |
| `requeue_for_retry`           | `RUNNING`                                                                |
| `requeue_bbox_refine`         | `REFINING_BBOXES`                                                        |

A worker that receives the same `IDPJobSubmitted` event twice — for
example because the bus redelivered while a peer was still claiming —
calls `mark_running` and gets `None` on the second call. It logs and
bails. No duplicate orchestrator invocation, no duplicate webhook.

### 2. Per-group advisory lock on the EDA drain (`PostgresEventBus`)

The Postgres EDA adapter (in `fireflyframework-pyfly`) wraps every
drain pass in `pg_try_advisory_lock(group_key)`. The key is a
deterministic SHA-256 fold of the consumer-group name. Concurrent
replicas in the same group all attempt the lock; whoever wins drains
the outbox; everyone else returns immediately and waits for the next
`NOTIFY` or poll tick.

This is the layer that *prevents* the duplicate-dispatch problem in
the first place. Layer 1 (atomic claim) is the defense-in-depth that
makes the system correct even if a future bus adapter drops it.

Session-level lock → auto-releases on connection death. A worker that
crashes mid-drain never zombies the group.

### 3. Idempotency-key collision recovery (`SubmitJobHandler`)

The submit path is `SELECT-by-key` then `INSERT`. Two concurrent
requests with the same `Idempotency-Key` can both miss the SELECT and
both attempt the INSERT. One wins; the other hits the partial unique
index `uq_extraction_jobs_idempotency_key` and the SDK / handler
catches `IntegrityError`, re-resolves the winning row, and returns the
idempotent `SubmitJobResponse` shape. The caller sees no difference
between winning and losing.

### 4. Periodic reaper revives orphans (`JobReaper` + `BboxReaper`)

Atomic claim solves *duplicate processing*. The reaper solves the
opposite failure mode: when the event chain breaks and a job is stuck
because **nothing** is going to deliver to it.

The five orphan classes the reaper handles:

| #  | Status                 | Cause                                                                    | Reaper       |
| -- | ---------------------- | ------------------------------------------------------------------------ | ------------ |
| 1  | `QUEUED`               | Submit handler crashed between row INSERT and outbox PUBLISH             | `JobReaper`  |
| 2  | `RUNNING`              | Worker crashed mid-extraction past its lease                              | `JobReaper`  |
| 3  | `QUEUED` (post-retry)  | Worker's `_delayed_publish` task died before its `asyncio.sleep` completed | `JobReaper`  |
| 4  | `PARTIAL_SUCCEEDED`    | Main worker crashed between `mark_partial_succeeded` and publish-bbox    | `BboxReaper` |
| 5  | `REFINING_BBOXES`      | Bbox worker crashed mid-grounding                                         | `BboxReaper` |

Every `reaper_sweep_interval_s` (default 60 s) each reaper:

1. Queries rows that match each orphan signature.
2. Republishes a fresh EDA event for each id.
3. Lets the atomic claim from invariant #1 pick exactly one consumer.

Duplicate republishes from multiple reaper replicas all funnel through
the atomic claim, so running a reaper in every worker container is
safe.

---

## Lease windows

A "lease" is the wall-clock window during which a `RUNNING` (or
`REFINING_BBOXES`) row is considered legitimately owned by its
claimant. Past the lease, the reaper assumes the claimant is dead and
republishes. The next claim succeeds because `mark_running` matches
`RUNNING WITH stale started_at`.

| Setting                          | Default | What it means                                                          |
| -------------------------------- | ------- | ---------------------------------------------------------------------- |
| `FLYDOCS_JOB_RUN_LEASE_S`        | 1260    | `async_timeout_s + 60s`. The worker's own `asyncio.wait_for` caps any legitimate run at `async_timeout_s`, so a lease past that means crash. |
| `FLYDOCS_BBOX_REFINE_LEASE_S`    | 660     | `bbox_refine_timeout_s + 60s`. Same idea for the bbox leg.             |
| `FLYDOCS_REAPER_SWEEP_INTERVAL_S` | 60     | How often each reaper polls for stuck rows.                            |
| `FLYDOCS_QUEUED_ORPHAN_THRESHOLD_S` | 600  | `2 * retry_max_delay_s`. How long a `QUEUED` row waits before the reaper considers its triggering event lost. |
| `FLYDOCS_PARTIAL_SUCCEEDED_ORPHAN_THRESHOLD_S` | 1320 | `async_timeout_s + 120s`. How long after the main extraction's `started_at` we conclude the bbox-refine event was lost. |

Recovery time after a crash is bounded by `lease + reaper_sweep_interval_s`
≈ 22 min with the shipped defaults and `async_timeout_s=1200`. Lower
`async_timeout_s` for faster recovery on a use case where extraction
should be quick.

---

## What the reaper does NOT do

- **It doesn't dedupe republishes within a single sweep window.**
  A backlog of 50 jobs that are legitimately QUEUED for >
  `queued_orphan_threshold_s` will get republished on every 60 s
  sweep until they're picked up. That's bounded outbox bloat under
  heavy load, not unbounded. If this matters in your deployment,
  raise the threshold.
- **It doesn't fence multiple reaper replicas.** Two reapers in two
  containers both find the same stale rows; both republish; the
  atomic claim dedupes the work. Cost is the extra outbox INSERTs
  and `NOTIFY` traffic.
- **It doesn't cancel a stuck `RUNNING` job.** Mid-flight cancel is
  intentionally not supported (the orchestrator has no cancellation
  hook). To kill a stuck job today, wait for the lease + reaper to
  revive it, then issue cancel against the redelivered QUEUED entry.

---

## Recipes

### Run more than one worker safely

`docker compose --scale worker=3` (or the equivalent in K8s).
Everything is bounded by the four invariants above. No coordination
config to set.

### Detect orphans manually

```sql
-- Stuck RUNNING (lease expired)
SELECT id, started_at, attempts FROM extraction_jobs
WHERE status='RUNNING'
  AND started_at < now() - INTERVAL '21 minutes'
ORDER BY started_at;

-- QUEUED with no event in outbox (submit/retry-publish lost)
SELECT id, created_at FROM extraction_jobs
WHERE status='QUEUED'
  AND COALESCE(started_at, created_at) < now() - INTERVAL '10 minutes'
ORDER BY created_at;

-- PARTIAL_SUCCEEDED waiting on a bbox event that may have been lost
SELECT id, started_at FROM extraction_jobs
WHERE status='PARTIAL_SUCCEEDED'
  AND bbox_refine_status='pending'
  AND bbox_refine_started_at IS NULL
  AND started_at < now() - INTERVAL '22 minutes';
```

The reaper would have caught all of these before the next sweep
completes; the queries are useful for ad-hoc audits.

### Force a re-claim now (operator override)

Trim `started_at` to a past instant; the reaper's next sweep picks
the row up:

```sql
UPDATE extraction_jobs
SET started_at = now() - INTERVAL '24 hours'
WHERE id = '<job-id>' AND status='RUNNING';
```

---

## Adapter compatibility

| EDA adapter (`FLYDOCS_EDA_ADAPTER`) | Multi-worker safe? | Notes |
| ----------------------------------- | ------------------ | ----- |
| `postgres` (default)                | ✅ Yes              | Via per-group `pg_try_advisory_lock` in pyfly's adapter. |
| `redis`                             | ✅ Yes              | Redis Streams `XREADGROUP` is a competitive consumer by design. |
| `kafka`                             | ✅ Yes              | Kafka consumer groups partition delivery across replicas. |
| `memory`                            | ❌ Single-process only | Process-local queue; not for multi-replica deployments. |

The repository-level atomic claim is adapter-agnostic — even with the
`memory` adapter in tests, the same `mark_running` precondition wins.
