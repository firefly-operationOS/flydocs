# flydocs Java SDK

Official Java/Spring Boot client for [flydocs](https://github.com/firefly-operationOS/flydocs) — the pure-multimodal Intelligent Document Processing service from Firefly OperationOS.

- **Java 25** toolchain (compiled to release 25).
- **Spring Boot 3.x** managed dependencies — drops cleanly into any Boot 3.x app.
- **Reactive WebClient** with a blocking `FlydocsClient` facade.
- **`flydocs-spring-boot-starter`** — drop-in autoconfig driven by `flydocs.*` properties, including a `@FlydocsWebhook` argument resolver that injects signature-verified `EventEnvelope`s into controller methods.
- **Records** for every DTO, snake_case on the wire via Jackson `@JsonProperty`, idiomatic camelCase in Java.
- **Typed errors** mapping the service's RFC 7807 problem-details.
- **HMAC webhook verifier** with constant-time comparison.
- **Opt-in retries** for transient 5xx + timeouts with exponential backoff.
- **`AutoCloseable`** — own the Netty pool lifecycle explicitly, or let the starter manage it.

## Modules

| Artifact                          | Use it when …                                          |
|-----------------------------------|--------------------------------------------------------|
| `flydocs-sdk`                     | You're not on Spring Boot, or you want to build the client manually. |
| `flydocs-spring-boot-starter`     | You're on Boot 3.x and want the client autowired from `flydocs.*` properties. Pulls in `flydocs-sdk` transitively. |
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
  <version>26.6.0</version>
</dependency>

<!-- ...OR Spring Boot starter (recommended on Boot 3.x) -->
<dependency>
  <groupId>com.firefly.flydocs</groupId>
  <artifactId>flydocs-spring-boot-starter</artifactId>
  <version>26.6.0</version>
</dependency>
```

## Quickstart — Spring Boot autoconfig

```yaml
# application.yaml
flydocs:
  base-url: http://localhost:8400
  api-key: ${FLYDOCS_API_KEY}             # optional; sent as Authorization: Bearer …
  timeout: 60s
  max-attempts: 3                          # retry transient 5xx with exponential backoff
  webhook:
    secret: ${FLYDOCS_WEBHOOK_SECRET}      # optional; only set if you receive webhooks
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

The starter publishes a `FlydocsClientAsync`, a `FlydocsClient`, and (when `flydocs.webhook.secret` is set) a `WebhookVerifier` plus a `FlydocsWebhookArgumentResolver`. The Netty pool is released cleanly on context shutdown.

## Quickstart — blocking (typed builders)

```java
import com.firefly.flydocs.sdk.FlydocsClient;
import com.firefly.flydocs.sdk.model.*;
import java.nio.file.Path;

FlydocsClient flydocs = FlydocsClient.builder()
        .baseUrl("http://localhost:8400")
        .apiKey(System.getenv("FLYDOCS_API_KEY"))
        .build();

DocumentTypeSpec invoice = DocumentTypeSpec.builder("invoice")
        .addFieldGroup("totals",
                Field.required("total_amount", FieldType.NUMBER),
                Field.required("currency",      FieldType.STRING))
        .build();

ExtractionRequest req = ExtractionRequest.builder()
        .addFile(FileInput.ofPath(Path.of("invoice.pdf")))
        .addDocumentType(invoice)
        .options(ExtractionOptions.builder()
                .stages(StageToggles.builder().judge(true).bboxRefine(true).build())
                .build())
        .build();

ExtractionResult result = flydocs.extract(req);
System.out.printf("id=%s   status=%s   model=%s   latency=%dms%n",
        result.id(), result.status(),
        result.pipeline().model(), result.pipeline().latencyMs());
```

> **See [TUTORIAL.md](./TUTORIAL.md) for the full walkthrough** — schemas, rules, async extractions, webhooks, errors, reactive usage.

## Quickstart — reactive (with `waitForCompletion`)

```java
import com.firefly.flydocs.sdk.FlydocsClientAsync;
import com.firefly.flydocs.sdk.model.*;
import java.time.Duration;

FlydocsClientAsync flydocs = FlydocsClientAsync.builder()
        .baseUrl("http://localhost:8400")
        .build();

flydocs.extractions().create(submitRequest, "my-app:invoice:42")
        .doOnNext(r -> log.info("queued {}", r.id()))
        .flatMap(submit -> flydocs.extractions().waitForCompletion(
                submit.id(),
                Duration.ofSeconds(2),
                Duration.ofMinutes(10)))
        .filter(s -> s.status() == ExtractionStatus.SUCCEEDED)
        .flatMap(s -> flydocs.extractions().getResult(s.id()))
        .subscribe(envelope ->
                log.info("got {} documents", envelope.result().documents().size()));
```

## Webhook verification (manual)

```java
import com.firefly.flydocs.sdk.webhook.WebhookVerifier;
import com.firefly.flydocs.sdk.webhook.WebhookVerificationException;

WebhookVerifier verifier = new WebhookVerifier(System.getenv("FLYDOCS_WEBHOOK_SECRET"));

@PostMapping(value = "/flydocs/webhook", consumes = APPLICATION_JSON_VALUE)
public ResponseEntity<Void> onWebhook(
        @RequestHeader("X-Flydocs-Signature") String signature,
        HttpEntity<byte[]> body) {
    try {
        verifier.verify(body.getBody(), signature);
    } catch (WebhookVerificationException e) {
        return ResponseEntity.status(HttpStatus.FORBIDDEN).build();
    }
    // ... parse the JSON onto EventEnvelope, handle event_type, etc.
    return ResponseEntity.accepted().build();
}
```

## Webhook verification (Spring Boot starter)

The starter ships a `@FlydocsWebhook` annotation + argument resolver. The starter verifies the `X-Flydocs-Signature` HMAC and deserialises the body onto an `EventEnvelope` record before your controller method ever runs:

```java
@PostMapping("/flydocs/webhook")
public ResponseEntity<Void> onWebhook(@FlydocsWebhook EventEnvelope event) {
    if (EventEnvelope.TYPE_EXTRACTION_COMPLETED.equals(event.eventType())) {
        // ... event.extraction(), event.result(), event.metadata()
    }
    return ResponseEntity.accepted().build();
}
```

## API surface

| SDK method                                              | HTTP                                       | Returns                       |
|---------------------------------------------------------|--------------------------------------------|-------------------------------|
| `extract(req)`                                          | `POST /api/v1/extract`                     | `ExtractionResult`            |
| `validate(req)`                                         | `POST /api/v1/extract:validate`            | `Map<String, Object>`         |
| `extractions().create(req, idemKey)`                    | `POST /api/v1/extractions`                 | `Extraction`                  |
| `extractions().get(id)`                                 | `GET  /api/v1/extractions/{id}`            | `Extraction`                  |
| `extractions().cancel(id)`                              | `DELETE /api/v1/extractions/{id}`          | `Extraction`                  |
| `extractions().getResult(id, waitForBboxes, t)`         | `GET  /api/v1/extractions/{id}/result`     | `ExtractionResultEnvelope`    |
| `extractions().list(query)`                             | `GET  /api/v1/extractions`                 | `ExtractionListResponse`      |
| `extractions().waitForCompletion(id, poll, t)`          | polls `GET /api/v1/extractions/{id}`       | terminal `Extraction`         |
| `version()`                                             | `GET  /api/v1/version`                     | `VersionInfo`                 |
| `health(probe)`                                         | `GET  /actuator/health/{probe}`            | `Map<String, Object>`         |

## Typed request types

| Type                     | Purpose                                                                    |
|--------------------------|----------------------------------------------------------------------------|
| `StageToggles`           | Opt-in switches for every optional pipeline stage. Has a fluent `builder()`. |
| `ExtractionOptions`      | Per-request knobs, including `escalation` sub-object. Has a fluent `builder()`. |
| `DocumentTypeSpec`       | One expected document type. Has a fluent `builder()`.                      |
| `FieldGroup`, `Field`    | Field schema. Single recursive `Field` covers primitives, arrays, objects. |
| `ValidatorSpec`          | Built-in field validator (`iban`, `bic`, `vat_id`, …).                     |
| `RuleSpec` + `RuleParent` (sealed: `Field`/`Validator`/`Rule`) | Business-rule DAG.                          |
| `Transformation` (sealed: `EntityResolutionTransformation`/`LlmTransformation`) | Post-extraction transformations. |
| `ExtractionRequest.builder()` / `SubmitExtractionRequest.builder()` | Top-level request builders.            |

## Errors

Every error subclasses `FlydocsException`:

| Class                     | When                                                    |
|---------------------------|---------------------------------------------------------|
| `FlydocsTimeoutException` | SDK's own HTTP timeout fired (no service response).     |
| `FlydocsClientException`  | Other transport failure (DNS, connect, TLS).            |
| `FlydocsHttpException`    | Service returned 4xx/5xx. Carries `statusCode()`, `code()`, `title()`, `detail()`, and the raw `payload()` map. |

```java
try {
    flydocs.extract(req);
} catch (FlydocsHttpException e) {
    if ("timeout".equals(e.code())) {
        flydocs.extractions().create(submitReq);   // fall back to async
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

Apache-2.0. Copyright (c) 2026 Firefly Software Solutions Inc.
