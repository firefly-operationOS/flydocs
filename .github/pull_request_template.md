<!--
  Thanks for opening a PR! A few things to keep the review fast:

  * Keep the scope tight. Bundle unrelated changes into separate PRs.
  * Update docs / Taskfile / env_template when behaviour changes.
  * If you touch the EDA / pipeline orchestrator, attach a smoke run.
-->

## Summary

<!-- One paragraph: what changes, and why now. -->

## Changes

<!-- Bullet list of the concrete things this PR does. -->

-

## Verification

<!--
  Tick what you ran. Real verification (real Postgres, real Anthropic,
  the escritura PDF) is the gold standard for anything touching the
  pipeline or EDA. Stubbed runs are fine for surface fixes.
-->

- [ ] `task lint:check` passes
- [ ] `task test:unit` passes (94 tests)
- [ ] `task docker:up` boots and `/actuator/health/readiness` returns 200 with `database_health` + `eda_health` UP
- [ ] (if pipeline / EDA changes) ran `scripts/smoke_async_postgres_eda.sh` against the escritura PDF and the full pipeline succeeds

## Notes for the reviewer

<!--
  Anything subtle. Schema changes, backward-compat concerns, places
  to look first, follow-up work captured in tickets.
-->
