# flydocs Java SDK — Tutorial

A complete walkthrough of the flydocs Java/Spring Boot SDK against the v1 API. Every section is a small, runnable snippet.

> **Prerequisites**
> A flydocs service reachable at some base URL. For local development:
> ```bash
> task docker:up:test    # starts flydocs + a mock LLM at http://localhost:8080
> ```
> Java 25 + Maven 3.9+ on the build host.

---

## Table of contents

1. [Install](#1-install)
2. [Your first extraction](#2-your-first-extraction)
3. [Designing a schema with `DocumentTypeSpec` builders](#3-designing-a-schema-with-documenttypespec-builders)
4. [Tuning the pipeline with `StageToggles`](#4-tuning-the-pipeline-with-stagetoggles)
5. [Adding business rules](#5-adding-business-rules)
6. [Asynchronous extraction with `waitForCompletion`](#6-asynchronous-extraction-with-waitforcompletion)
7. [Webhook delivery + signature verification](#7-webhook-delivery--signature-verification)
8. [Error handling — RFC 7807 problem-details](#8-error-handling--rfc-7807-problem-details)
9. [Spring Boot starter](#9-spring-boot-starter)
10. [Resilience knobs (opt-in retry + pool sizing)](#10-resilience-knobs-opt-in-retry--pool-sizing)
11. [Reactive usage](#11-reactive-usage)

---

## 1. Install

The artifact is published to GitHub Packages on every `vX.Y.Z` tag.

Add the server credentials to `~/.m2/settings.xml`:

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

Add the repository + dependency to your `pom.xml`:

```xml
<repositories>
  <repository>
    <id>github</id>
    <url>https://maven.pkg.github.com/firefly-operationOS/flydocs</url>
    <snapshots><enabled>true</enabled></snapshots>
  </repository>
</repositories>

<dependency>
  <groupId>com.firefly.flydocs</groupId>
  <artifactId>flydocs-sdk</artifactId>
  <version>26.6.0</version>
</dependency>
```

---

## 2. Your first extraction

```java
import com.firefly.flydocs.sdk.FlydocsClient;
import com.firefly.flydocs.sdk.model.*;
import java.nio.file.Path;

FlydocsClient flydocs = FlydocsClient.builder()
        .baseUrl("http://localhost:8080")
        .build();

ExtractionRequest req = ExtractionRequest.builder()
        .addFile(FileInput.ofPath(Path.of("invoice.pdf")))
        .addDocumentType(DocumentTypeSpec.builder("invoice")
                .addFieldGroup("totals",
                        Field.required("total_amount", FieldType.NUMBER),
                        Field.required("currency",      FieldType.STRING))
                .build())
        .build();

ExtractionResult result = flydocs.extract(req);
System.out.printf("id=%s  status=%s  model=%s  latency=%dms%n",
        result.id(), result.status(),
        result.pipeline().model(), result.pipeline().latencyMs());
```

`FlydocsClient` is the blocking facade. Keep one instance per logical caller — the underlying `WebClient` is thread-safe and re-uses its connection pool.

---

## 3. Designing a schema with `DocumentTypeSpec` builders

Hand-built `Map` literals are unsafe — typos in keys ship to production. The SDK ships records + fluent builders for the full request-side schema.

```java
import com.firefly.flydocs.sdk.model.*;
import java.util.List;
import java.util.Map;

DocumentTypeSpec invoice = DocumentTypeSpec.builder("invoice")
        .description("Vendor invoice (paper or PDF)")
        .country("ES")
        .addFieldGroup("header",
                Field.required("invoice_number", FieldType.STRING),
                Field.builder("invoice_date")
                        .type(FieldType.STRING)
                        .required(true)
                        .format(StandardFormat.DATE)
                        .build(),
                Field.builder("supplier_vat")
                        .type(FieldType.STRING)
                        .validator(new ValidatorSpec("vat_id", Map.of("country", "ES")))
                        .build())
        .addFieldGroup("totals",
                Field.builder("subtotal").type(FieldType.NUMBER).required(true).minimum(0.0).build(),
                Field.builder("tax_amount").type(FieldType.NUMBER).required(true).minimum(0.0).build(),
                Field.builder("total_amount").type(FieldType.NUMBER).required(true).minimum(0.0).build(),
                Field.required("currency", FieldType.STRING))
        // Repeating rows: array of object (recursive Field).
        .addFieldGroup("line_items_block",
                Field.builder("line_items")
                        .type(FieldType.ARRAY)
                        .items(Field.builder("row")
                                .type(FieldType.OBJECT)
                                .fields(List.of(
                                        Field.of("description", FieldType.STRING),
                                        Field.of("quantity",    FieldType.NUMBER),
                                        Field.of("unit_price",  FieldType.NUMBER),
                                        Field.of("line_total",  FieldType.NUMBER)))
                                .build())
                        .build())
        .build();
```

Plug it straight into the request:

```java
ExtractionRequest req = ExtractionRequest.builder()
        .addFile(FileInput.ofPath(Path.of("invoice.pdf")))
        .addDocumentType(invoice)
        .build();
```

> **Validator catalogue.** `ValidatorSpec.name()` is a free string so the SDK never gates on a stale list. Canonical names: `iban`, `bic`, `credit_card`, `phone_e164`, `vat_id`, `nif`, `nie`, `cif`, `uuid`, `date`, `date-time`, `email`, `uri`, `url`, `ipv4`, `ipv6`, `domain`, `slug`, `passport_number`. Pass extras via `params` (e.g. `country`).

---

## 4. Tuning the pipeline with `StageToggles`

```java
ExtractionOptions options = ExtractionOptions.builder()
        .returnBboxes(true)
        .languageHint("es")
        .model("anthropic:claude-sonnet-4-6")
        .stages(StageToggles.builder()
                .classifier(true)
                .fieldValidation(true)
                .judge(true)
                .bboxRefine(true)
                .ruleEngine(true)
                .build())
        // v1: escalation is a sub-object, not a flat threshold+model pair.
        .escalation(0.25, "anthropic:claude-opus-4-7")
        .build();

ExtractionRequest req = ExtractionRequest.builder()
        .addFile(FileInput.ofPath(Path.of("invoice.pdf")))
        .addDocumentType(invoice)
        .options(options)
        .build();
```

Use `StageToggles.defaults()` if you want the service's default behaviour explicitly.

---

## 5. Adding business rules

```java
RuleSpec totalsConsistent = RuleSpec.builder(
                "totals_consistent",
                "subtotal + tax_amount equals total_amount within 0.01")
        .addFieldParent("invoice", "subtotal", "tax_amount", "total_amount")
        .build();

RuleSpec vatIdValid = RuleSpec.builder(
                "vat_id_valid",
                "The supplier VAT id passes the vat_id validator")
        .addValidatorParent("invoice", "vat_id")
        .build();

RuleSpec invoiceAcceptable = RuleSpec.builder(
                "invoice_acceptable",
                "totals are consistent AND the VAT id is valid")
        .addRuleParent("totals_consistent")
        .addRuleParent("vat_id_valid")
        .output(RuleOutputSpec.bool())
        .build();

ExtractionRequest req = ExtractionRequest.builder()
        .addFile(FileInput.ofPath(Path.of("invoice.pdf")))
        .addDocumentType(invoice)
        .addRule(totalsConsistent)
        .addRule(vatIdValid)
        .addRule(invoiceAcceptable)
        .options(ExtractionOptions.builder()
                .stages(StageToggles.builder()
                        .fieldValidation(true)
                        .ruleEngine(true)
                        .build())
                .build())
        .build();
```

In the response, each rule's resolved output lives under `result.ruleResults()` as a typed list of `RuleResult` records.

---

## 6. Asynchronous extraction with `waitForCompletion`

```java
import com.firefly.flydocs.sdk.model.*;
import java.time.Duration;

SubmitExtractionRequest submitReq = SubmitExtractionRequest.builder()
        .addFile(FileInput.ofPath(Path.of("big-batch.pdf")))
        .addDocumentType(invoice)
        .callbackUrl("https://your-app.example.com/flydocs/webhook")
        .metadata("caller", "ingest-pipeline")
        .metadata("batch_id", "b-42")
        .build();

Extraction submit = flydocs.extractions().create(submitReq, "ingest-pipeline:b-42");
log.info("queued {}", submit.id());

Extraction finalStatus = flydocs.extractions().waitForCompletion(
        submit.id(),
        Duration.ofSeconds(2),
        Duration.ofMinutes(15));

switch (finalStatus.status()) {
    case SUCCEEDED -> {
        ExtractionResult result = flydocs.extractions().getResult(submit.id()).result();
        log.info("done: {} documents, {}ms",
                result.documents().size(), result.pipeline().latencyMs());
    }
    case FAILED, CANCELLED -> log.error("extraction did not succeed: {} {}",
            finalStatus.status(),
            finalStatus.error() != null ? finalStatus.error().message() : "(no error block)");
    default -> log.warn("non-terminal status: {}", finalStatus.status());
}
```

`waitForCompletion` returns the final `Extraction` no matter the outcome (success or failure) — only `TimeoutException` is thrown, and only when the deadline elapses while the worker is still mid-flight. The v1 state machine simplifies to `queued -> running -> succeeded | failed | cancelled`; bbox refinement runs as additive post-processing under `finalStatus.postProcessing()`.

> **Idempotency.** Send the same `Idempotency-Key` to replay an existing submission instead of creating a duplicate extraction. The service indexes by key so retries are cheap.

---

## 7. Webhook delivery + signature verification

The starter ships a `@FlydocsWebhook` argument resolver that verifies the signature and deserialises the payload before your controller method runs:

```java
import com.firefly.flydocs.sdk.model.EventEnvelope;
import com.firefly.flydocs.sdk.spring.FlydocsWebhook;

@PostMapping("/flydocs/webhook")
public ResponseEntity<Void> onWebhook(@FlydocsWebhook EventEnvelope event) {
    if (EventEnvelope.TYPE_EXTRACTION_COMPLETED.equals(event.eventType())
            && event.result() != null) {
        // persist extracted fields, kick off downstream work, ...
    }
    return ResponseEntity.accepted().build();
}
```

Manual verification (no starter):

```java
import com.firefly.flydocs.sdk.webhook.WebhookVerifier;
import com.firefly.flydocs.sdk.webhook.WebhookVerificationException;
import com.firefly.flydocs.sdk.model.EventEnvelope;

WebhookVerifier verifier = new WebhookVerifier(System.getenv("FLYDOCS_WEBHOOK_SECRET"));

@PostMapping(value = "/flydocs/webhook", consumes = APPLICATION_JSON_VALUE)
public ResponseEntity<Void> onWebhook(
        @RequestHeader("X-Flydocs-Signature") String signature,
        HttpEntity<byte[]> body) throws JsonProcessingException {
    try {
        verifier.verify(body.getBody(), signature);
    } catch (WebhookVerificationException e) {
        return ResponseEntity.status(HttpStatus.FORBIDDEN).build();
    }
    EventEnvelope event = objectMapper.readValue(body.getBody(), EventEnvelope.class);
    if (event.extraction().status() == ExtractionStatus.SUCCEEDED && event.result() != null) {
        // ...
    }
    return ResponseEntity.accepted().build();
}
```

**Important:** verify against the *raw* request body bytes. If you let Spring deserialise the JSON into a record first, re-encoding will change the digest and the verification will fail. `HttpEntity<byte[]>` keeps the raw bytes available. The `@FlydocsWebhook` resolver does this for you.

---

## 8. Error handling — RFC 7807 problem-details

```java
import com.firefly.flydocs.sdk.error.*;

try {
    flydocs.extract(req);
} catch (FlydocsHttpException e) {
    switch (e.code()) {
        case "timeout" -> // fall back to async
                flydocs.extractions().create(submitReqFrom(req));
        case "file_too_large" -> throw e;          // split / compress the file
        case "validation_failed" -> {              // payload describes every issue
            log.error("validation failed: {}", e.payload());
            throw e;
        }
        default -> throw e;
    }
} catch (FlydocsTimeoutException e) {
    // SDK's own HTTP timeout — request never came back over the wire
} catch (FlydocsClientException e) {
    // other transport failure (DNS, connect, TLS)
}
```

Common `code` values:

| `code`                  | Status | Meaning                                                          |
|-------------------------|--------|------------------------------------------------------------------|
| `timeout`               | 408    | Pipeline exceeded the sync ceiling. Retry via async extractions. |
| `file_too_large`        | 413    | File over `FLYDOCS_MAX_BYTES`.                                   |
| `invalid_base64`        | 422    | `content_base64` failed strict parsing.                          |
| `validation_failed`     | 422    | Semantic validation found issues.                                |
| `not_ready`             | 409    | Result not available yet (status is queued/running/failed/cancelled). |
| `not_cancellable`       | 409    | Extraction has started; mid-flight cancellation isn't supported. |
| `not_found`             | 404    | Unknown extraction id.                                           |

---

## 9. Spring Boot starter

`flydocs-spring-boot-starter` autowires the client from `flydocs.*`
properties. The starter declares `@ConditionalOnClass(FlydocsClientAsync)`
+ `@ConditionalOnProperty("flydocs.base-url")`, so it only activates
when the SDK is on the classpath and the base URL is configured.

```xml
<dependency>
  <groupId>com.firefly.flydocs</groupId>
  <artifactId>flydocs-spring-boot-starter</artifactId>
  <version>26.6.0</version>
</dependency>
```

```yaml
# application.yaml
flydocs:
  base-url: http://localhost:8080
  api-key: ${FLYDOCS_API_KEY}                  # optional, Authorization: Bearer
  timeout: 60s
  max-attempts: 3                              # retry transient 5xx + timeouts
  retry-min-backoff: 200ms
  max-connections: 50
  pending-acquire-timeout: 45s
  max-in-memory-size: 67108864                 # 64 MiB
  tenant-id: my-tenant                         # optional X-Tenant-Id default
  webhook:
    secret: ${FLYDOCS_WEBHOOK_SECRET}          # optional; enables WebhookVerifier + @FlydocsWebhook
```

```java
@Service
class DocumentService {
  private final FlydocsClientAsync flydocs;  // autowired -- reactive
  private final FlydocsClient sync;          // autowired -- blocking facade
  private final WebhookVerifier verifier;    // only when webhook.secret is set

  DocumentService(FlydocsClientAsync flydocs, FlydocsClient sync, WebhookVerifier verifier) {
    this.flydocs = flydocs;
    this.sync = sync;
    this.verifier = verifier;
  }
}
```

Beans published by the starter:

| Bean                                  | Conditional on                              |
|---------------------------------------|---------------------------------------------|
| `FlydocsClientAsync`                  | `flydocs.base-url` set                      |
| `FlydocsClient`                       | `flydocs.base-url` set                      |
| `WebhookVerifier`                     | `flydocs.webhook.secret` set                |
| `FlydocsWebhookArgumentResolver`      | `flydocs.webhook.secret` set + Spring MVC   |

All three are `@ConditionalOnMissingBean`, so your own `@Bean` wins
trivially. The two client beans declare `destroyMethod="close"`, so
the underlying Netty `ConnectionProvider` is released cleanly when the
Spring context shuts down — no leaked threads on hot reload.

---

## 10. Resilience knobs (opt-in retry + pool sizing)

The builder exposes the same knobs the starter binds to properties.
Useful when you're constructing the client manually (CLI, integration
test, non-Spring app):

```java
FlydocsClientAsync flydocs = FlydocsClientAsync.builder()
        .baseUrl("http://localhost:8080")
        .apiKey(System.getenv("FLYDOCS_API_KEY"))
        .timeout(Duration.ofSeconds(60))
        .maxAttempts(3)                        // retry 5xx + timeouts
        .retryMinBackoff(Duration.ofMillis(200))  // exponential with jitter
        .maxConnections(50)
        .pendingAcquireTimeout(Duration.ofSeconds(45))
        .maxInMemorySize(64 * 1024 * 1024)
        .build();
```

Retry semantics:

- **Retried:** `FlydocsHttpException` with `statusCode >= 500`,
  `FlydocsTimeoutException`, `FlydocsClientException` (transport).
- **Not retried:** any 4xx (including `409 not_cancellable`,
  `422 validation_failed`). Bad requests stay bad on retry; intentional
  conflicts shouldn't be papered over.

Both `FlydocsClientAsync` and `FlydocsClient` implement
`AutoCloseable`. Use `try-with-resources` when you construct them
yourself; let Spring handle it when you use the starter.

```java
try (FlydocsClient flydocs = FlydocsClient.builder()
        .baseUrl("http://localhost:8080")
        .maxAttempts(3)
        .build()) {
    ExtractionResult result = flydocs.extract(req);
}
```

---

## 11. Reactive usage

If you're already on Project Reactor (WebFlux, R2DBC, Kafka reactive consumers), use `FlydocsClientAsync` directly — there's no blocking wrapper cost:

```java
import com.firefly.flydocs.sdk.FlydocsClientAsync;
import reactor.core.publisher.Mono;

FlydocsClientAsync flydocs = FlydocsClientAsync.builder()
        .baseUrl("http://localhost:8080")
        .build();

Mono<ExtractionResult> result = flydocs.extract(req, "my-idempotency-key", "my-correlation-id");

result.subscribe(r ->
        log.info("model={} latency={}ms documents={}",
                r.pipeline().model(), r.pipeline().latencyMs(), r.documents().size()));
```

The reactive client also exposes `extractions().waitForCompletion(id, pollInterval, timeout)` returning `Mono<Extraction>` — chain it into your reactive pipeline.

```java
Mono<ExtractionResultEnvelope> finalResult = flydocs.extractions().create(submitReq)
        .flatMap(submit -> flydocs.extractions().waitForCompletion(
                submit.id(),
                Duration.ofSeconds(2),
                Duration.ofMinutes(10)))
        .filter(s -> s.status() == ExtractionStatus.SUCCEEDED)
        .flatMap(s -> flydocs.extractions().getResult(s.id()));
```

---

## Further reading

- [`docs/api-reference.md`](../../docs/api-reference.md) — full HTTP wire contract.
- [`docs/pipeline.md`](../../docs/pipeline.md) — stage DAG, opt-in flags, what each stage does.
- [`docs/rule-engine.md`](../../docs/rule-engine.md) — rule semantics and DAG resolution.
- [`docs/validators.md`](../../docs/validators.md) — every built-in validator + parameters.
