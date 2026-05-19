# flydocs Java SDK — Examples

Runnable reactive examples mirroring the [Python SDK's example set](../../python/examples/) one-to-one. Each example is a single self-contained class; shared schemas + rules live in `ExampleHelpers`.

| # | Class                                                                                       | Mirrors                                | What it shows                                                                |
|---|---------------------------------------------------------------------------------------------|----------------------------------------|------------------------------------------------------------------------------|
| 1 | [`FirstExtractionExample`](./src/main/java/com/firefly/flydocs/examples/FirstExtractionExample.java)         | `01_first_extraction.py`               | Smallest reactive extraction; hand-written `DocSpec`; `try-with-resources` on the client. |
| 2 | [`TypedSchemaAndRulesExample`](./src/main/java/com/firefly/flydocs/examples/TypedSchemaAndRulesExample.java) | `02_typed_schema_and_rules.py`         | Realistic invoice schema + two business rules + opt-in `judge` + `ruleEngine` stages. |
| 3 | [`AsyncJobWithWaitExample`](./src/main/java/com/firefly/flydocs/examples/AsyncJobWithWaitExample.java)       | `03_async_job_with_wait.py`            | Submit + `waitForCompletion` + `getJobResult` as a single chained `Mono` pipeline. |
| 4 | [`WebhookReceiverApplication`](./src/main/java/com/firefly/flydocs/examples/WebhookReceiverApplication.java) | `04_webhook_receiver_fastapi.py`       | Spring Boot starter wiring + an annotated controller that verifies `X-Flydocs-Signature`. |
| 5 | [`ErrorHandlingExample`](./src/main/java/com/firefly/flydocs/examples/ErrorHandlingExample.java)             | `05_error_handling.py`                 | Typed `FlydocsHttpException` / `FlydocsTimeoutException`; sync→async fallback on `extraction_timeout`. |
| 6 | [`SyncFacadeExample`](./src/main/java/com/firefly/flydocs/examples/SyncFacadeExample.java)                   | `06_sync_facade.py`                    | The blocking `FlydocsClient` facade for non-reactive callers.                |

Plus [`ExampleHelpers`](./src/main/java/com/firefly/flydocs/examples/ExampleHelpers.java) — the `examples_helpers.py` analogue (shared invoice schema + rules + base-URL resolution).

## Running

Spin up a local flydocs first:

```bash
task docker:up:test     # serves http://localhost:8400 backed by the mock LLM
```

Then run any example. The plain extractor / async / sync ones take a PDF path as `Dexec.args`:

```bash
mvn -pl flydocs-examples compile exec:java \
  -Dexec.mainClass=com.firefly.flydocs.examples.FirstExtractionExample \
  -Dexec.args="path/to/invoice.pdf"
```

```bash
mvn -pl flydocs-examples compile exec:java \
  -Dexec.mainClass=com.firefly.flydocs.examples.TypedSchemaAndRulesExample \
  -Dexec.args="path/to/invoice.pdf"
```

```bash
mvn -pl flydocs-examples compile exec:java \
  -Dexec.mainClass=com.firefly.flydocs.examples.AsyncJobWithWaitExample \
  -Dexec.args="path/to/document.pdf"
```

```bash
mvn -pl flydocs-examples compile exec:java \
  -Dexec.mainClass=com.firefly.flydocs.examples.ErrorHandlingExample \
  -Dexec.args="path/to/invoice.pdf"
```

```bash
mvn -pl flydocs-examples compile exec:java \
  -Dexec.mainClass=com.firefly.flydocs.examples.SyncFacadeExample \
  -Dexec.args="path/to/invoice.pdf"
```

The webhook receiver is a Spring Boot app — run it with `spring-boot:run`:

```bash
FLYDOCS_BASE_URL=http://localhost:8400 \
FLYDOCS_WEBHOOK_HMAC_SECRET=super-secret \
  mvn -pl flydocs-examples spring-boot:run \
    -Dspring-boot.run.mainClass=com.firefly.flydocs.examples.WebhookReceiverApplication
```

Then POST a flydocs-signed webhook body to `http://localhost:8080/flydocs/webhook` with the `X-Flydocs-Signature` header set to `sha256=<hex>`. The receiver returns `202` on a valid signature, `403` otherwise.

## Configuration

Every example reads `FLYDOCS_BASE_URL` from the environment; if unset it defaults to `http://localhost:8400`. Point at any flydocs deployment to run against real infrastructure.

The mock LLM that `task docker:up:test` brings up accepts any document and returns a fixed schema-compatible response, so the examples work end-to-end without an Anthropic / OpenAI key.

## Not deployed

`flydocs-examples` carries `<maven.deploy.skip>true</maven.deploy.skip>` so the module is compile-checked in CI but never published to GitHub Packages.
