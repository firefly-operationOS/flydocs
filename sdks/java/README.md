# flydocs Java SDK

Official Java/Spring Boot client for [flydocs](https://github.com/firefly-operationOS/flydocs) — the pure-multimodal Intelligent Document Processing service from Firefly OperationOS.

- **Java 25** toolchain (compiled to release 25).
- **Spring Boot 3.5.x** managed dependencies — drops cleanly into any Boot 3.5 app.
- **Reactive WebClient** with a blocking `FlydocsClient` facade.
- **`flydocs-spring-boot-starter`** — drop-in autoconfig driven by `flydocs.*` properties.
- **Records** for every DTO, immutable + null-tolerant.
- **Typed errors** mapping the service's RFC 7807 problem-details.
- **HMAC webhook verifier** with constant-time comparison.
- **Opt-in retries** for transient 5xx + timeouts with exponential backoff.
- **`AutoCloseable`** — own the Netty pool lifecycle explicitly, or let the starter manage it.

## Modules

| Artifact                          | Use it when …                                          |
|-----------------------------------|--------------------------------------------------------|
| `flydocs-sdk`                     | You're not on Spring Boot, or you want to build the client manually. |
| `flydocs-spring-boot-starter`     | You're on Boot 3.5.x and want the client autowired from `flydocs.*` properties. Pulls in `flydocs-sdk` transitively. |
| `flydocs-examples`                | Runnable, compile-checked examples; not deployed.      |

## Install (Maven)

The artifact is published to GitHub Packages.

Add the server credentials to your `~/.m2/settings.xml`:

```xml
<servers>
  <server>
    <id>github</id>
    <username>YOUR_GITHUB_USER</username>
    <!-- Personal access token with `read:packages` scope. -->
    <password>YOUR_GITHUB_PAT</password>
  </server>
</servers>
```

Then in your project's `pom.xml`:

```xml
<repositories>
  <repository>
    <id>github</id>
    <url>https://maven.pkg.github.com/firefly-operationOS/flydocs</url>
    <snapshots><enabled>true</enabled></snapshots>
  </repository>
</repositories>

<!-- Plain SDK -->
<dependency>
  <groupId>com.firefly.flydocs</groupId>
  <artifactId>flydocs-sdk</artifactId>
  <version>26.05.02</version>
</dependency>

<!-- ...OR Spring Boot starter (recommended on Boot 3.5.x) -->
<dependency>
  <groupId>com.firefly.flydocs</groupId>
  <artifactId>flydocs-spring-boot-starter</artifactId>
  <version>26.05.02</version>
</dependency>
```

## Quickstart — Spring Boot autoconfig

```yaml
# application.yaml
flydocs:
  base-url: http://localhost:8400
  timeout: 60s
  max-attempts: 3                 # retry transient 5xx with exponential backoff
  webhook:
    hmac-secret: ${FLYDOCS_WEBHOOK_HMAC_SECRET}     # optional; only set if you receive webhooks
```

```java
@RestController
class MyController {
  private final FlydocsClientAsync flydocs;     // autowired by the starter
  MyController(FlydocsClientAsync flydocs) { this.flydocs = flydocs; }

  @PostMapping("/extract")
  public Mono<ExtractionResult> extract(@RequestBody ExtractionRequest req) {
    return flydocs.extract(req);
  }
}
```

The starter publishes a `FlydocsClientAsync`, a `FlydocsClient`, and (when `flydocs.webhook.hmac-secret` is set) a `WebhookVerifier`. The Netty pool is released cleanly on context shutdown.

## Quickstart — blocking (typed builders)

```java
import com.firefly.flydocs.sdk.FlydocsClient;
import com.firefly.flydocs.sdk.model.*;
import java.nio.file.Path;

FlydocsClient flydocs = FlydocsClient.builder()
        .baseUrl("http://localhost:8400")
        .build();

DocSpec invoice = DocSpec.builder("invoice")
        .addFieldGroup("totals",
                FieldSpec.required("total_amount", FieldType.NUMBER),
                FieldSpec.required("currency",      FieldType.STRING))
        .build();

ExtractionRequest req = ExtractionRequest.builder()
        .addDocument(DocumentInput.ofPath(Path.of("invoice.pdf")))
        .addDocSpec(invoice)
        .options(ExtractionOptions.builder()
                .stages(StageToggles.builder().judge(true).bboxRefine(true).build())
                .build())
        .build();

ExtractionResult result = flydocs.extract(req);
System.out.printf("model=%s   latency=%dms%n", result.model(), result.latencyMs());
```

> **See [TUTORIAL.md](./TUTORIAL.md) for the full walkthrough** — schemas, rules, async jobs, webhooks, errors, reactive usage.

## Quickstart — reactive (with `waitForCompletion`)

```java
import com.firefly.flydocs.sdk.FlydocsClientAsync;
import com.firefly.flydocs.sdk.model.*;
import java.time.Duration;

FlydocsClientAsync flydocs = FlydocsClientAsync.builder()
        .baseUrl("http://localhost:8400")
        .build();

flydocs.submitJob(submitRequest, "my-app:invoice:42", null)
        .doOnNext(r -> log.info("queued {}", r.jobId()))
        .flatMap(submit -> flydocs.waitForCompletion(
                submit.jobId(),
                Duration.ofSeconds(2),
                Duration.ofMinutes(10)))
        .filter(s -> s.status() == JobStatus.SUCCEEDED)
        .flatMap(s -> flydocs.getJobResult(s.jobId()))
        .subscribe(jobResult ->
                log.info("got {} documents", jobResult.result().documents().size()));
```

## Webhook verification

```java
import com.firefly.flydocs.sdk.webhook.WebhookVerifier;
import com.firefly.flydocs.sdk.webhook.WebhookVerificationException;

WebhookVerifier verifier = new WebhookVerifier(System.getenv("FLYDOCS_WEBHOOK_HMAC_SECRET"));

// In your Spring controller:
@PostMapping(value = "/flydocs/webhook", consumes = APPLICATION_JSON_VALUE)
public ResponseEntity<Void> onWebhook(
        @RequestHeader("X-Flydocs-Signature") String signature,
        HttpEntity<byte[]> body) {
    try {
        verifier.verify(body.getBody(), signature);
    } catch (WebhookVerificationException e) {
        return ResponseEntity.status(HttpStatus.FORBIDDEN).build();
    }
    // ... handle payload
    return ResponseEntity.accepted().build();
}
```

## API surface

| SDK method                            | HTTP                                      | Returns                |
|---------------------------------------|-------------------------------------------|------------------------|
| `extract(req)`                        | `POST /api/v1/extract`                    | `ExtractionResult`     |
| `validate(req)`                       | `POST /api/v1/extract:validate`           | `Map<String, Object>`  |
| `submitJob(req)`                      | `POST /api/v1/jobs`                       | `SubmitJobResponse`    |
| `getJob(id)`                          | `GET  /api/v1/jobs/{id}`                  | `JobStatusResponse`    |
| `cancelJob(id)`                       | `DELETE /api/v1/jobs/{id}`                | `JobStatusResponse`    |
| `getJobResult(id, waitForBboxes, t)`  | `GET  /api/v1/jobs/{id}/result`           | `JobResult`            |
| `listJobs(filter)`                    | `GET  /api/v1/jobs`                       | `JobListResponse`      |
| `waitForCompletion(id, poll, t)`      | polls `GET /api/v1/jobs/{id}`             | terminal `JobStatusResponse` |
| `version()`                           | `GET  /api/v1/version`                    | `VersionInfo`          |
| `health(probe)`                       | `GET  /actuator/health/{probe}`           | `Map<String, Object>`  |

## Typed request types

| Type                       | Purpose                                                                       |
|----------------------------|-------------------------------------------------------------------------------|
| `StageToggles`             | Opt-in switches for every optional pipeline stage. Has a fluent `builder()`. |
| `ExtractionOptions`        | Per-request knobs. Has a fluent `builder()`.                                  |
| `DocSpec`                  | One expected document type. Has a fluent `builder()`.                         |
| `FieldGroup`, `FieldSpec`, `FieldItem` | Field schema. `FieldSpec` has a fluent `builder()`.               |
| `StandardValidatorSpec`    | Built-in field validator (IBAN, BIC, VAT_ID, …).                              |
| `RuleSpec` + `RuleParent` (sealed: `FieldParent`/`ValidatorParent`/`RuleRef`) | Business-rule DAG. |
| `ExtractionRequest.builder()` / `SubmitJobRequest.builder()`             | Top-level request fluent builders. |

## Errors

Every error subclasses `FlydocsException`:

| Class                    | When                                                    |
|--------------------------|---------------------------------------------------------|
| `FlydocsTimeoutException` | SDK's own HTTP timeout fired (no service response).     |
| `FlydocsClientException`  | Other transport failure (DNS, connect, TLS).            |
| `FlydocsHttpException`    | Service returned 4xx/5xx. Carries `statusCode()`, `code()`, `title()`, `detail()`, and the raw `payload()` map. |

```java
try {
    flydocs.extract(req);
} catch (FlydocsHttpException e) {
    if ("extraction_timeout".equals(e.code())) {
        flydocs.submitJob(submitReq);   // fall back to async
    }
}
```

## Examples

Six runnable examples live in [`flydocs-examples/`](./flydocs-examples), in 1:1 parity with the [Python SDK's examples](../python/examples/):

| Class                         | Mirrors                                  |
|-------------------------------|------------------------------------------|
| `FirstExtractionExample`      | `01_first_extraction.py`                 |
| `TypedSchemaAndRulesExample`  | `02_typed_schema_and_rules.py`           |
| `AsyncJobWithWaitExample`     | `03_async_job_with_wait.py`              |
| `WebhookReceiverApplication`  | `04_webhook_receiver_fastapi.py`         |
| `ErrorHandlingExample`        | `05_error_handling.py`                   |
| `SyncFacadeExample`           | `06_sync_facade.py`                      |

Run an example with:

```bash
mvn -pl flydocs-examples compile exec:java \
  -Dexec.mainClass=com.firefly.flydocs.examples.FirstExtractionExample \
  -Dexec.args="path/to/invoice.pdf"
```

## Build + test locally

```bash
cd sdks/java
mvn verify                                              # core + starter unit tests

# Live integration tests against a running service (tag-gated):
FLYDOCS_BASE_URL=http://localhost:8400 \
  mvn -pl flydocs-sdk test -Dgroups=integration
```

## License

Apache-2.0. Copyright © 2026 Firefly Software Solutions Inc.
