# flydocs Java SDK — Tutorial

A complete walkthrough of the flydocs Java/Spring Boot SDK. Every section is a small, runnable snippet.

> **Prerequisites**
> A flydocs service reachable at some base URL. For local development:
> ```bash
> task docker:up:test    # starts flydocs + a mock LLM at http://localhost:8400
> ```
> Java 25 + Maven 3.9+ on the build host.

---

## Table of contents

1. [Install](#1-install)
2. [Your first extraction](#2-your-first-extraction)
3. [Designing a schema with `DocSpec` builders](#3-designing-a-schema-with-docspec-builders)
4. [Tuning the pipeline with `StageToggles`](#4-tuning-the-pipeline-with-stagetoggles)
5. [Adding business rules](#5-adding-business-rules)
6. [Asynchronous extraction with `waitForCompletion`](#6-asynchronous-extraction-with-waitforcompletion)
7. [Webhook delivery + signature verification](#7-webhook-delivery--signature-verification)
8. [Error handling — RFC 7807 problem-details](#8-error-handling--rfc-7807-problem-details)
9. [Reactive usage](#9-reactive-usage)

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
  <version>26.05.01</version>
</dependency>
```

---

## 2. Your first extraction

```java
import com.firefly.flydocs.sdk.FlydocsClient;
import com.firefly.flydocs.sdk.model.*;
import java.nio.file.Path;
import java.util.List;

FlydocsClient flydocs = FlydocsClient.builder()
        .baseUrl("http://localhost:8400")
        .build();

ExtractionRequest req = ExtractionRequest.builder()
        .addDocument(DocumentInput.ofPath(Path.of("invoice.pdf")))
        .addDocSpec(DocSpec.builder("invoice")
                .addFieldGroup("totals",
                        FieldSpec.required("total_amount", FieldType.NUMBER),
                        FieldSpec.required("currency",      FieldType.STRING))
                .build())
        .build();

ExtractionResult result = flydocs.extract(req);
System.out.printf("model=%s   latency=%dms%n", result.model(), result.latencyMs());
```

`FlydocsClient` is the blocking facade. Keep one instance per logical caller — the underlying `WebClient` is thread-safe and re-uses its connection pool.

---

## 3. Designing a schema with `DocSpec` builders

Hand-built `Map` literals are unsafe — typos in keys ship to production. The SDK ships records + fluent builders for the full request-side schema.

```java
import com.firefly.flydocs.sdk.model.*;
import java.util.List;
import java.util.Map;

DocSpec invoice = DocSpec.builder("invoice")
        .description("Vendor invoice (paper or PDF)")
        .country("ES")
        .addFieldGroup("header",
                FieldSpec.required("invoice_number", FieldType.STRING),
                FieldSpec.builder("invoice_date")
                        .type(FieldType.STRING)
                        .required(true)
                        .format(StandardFormat.DATE)
                        .build(),
                FieldSpec.builder("supplier_vat")
                        .type(FieldType.STRING)
                        .validator(new StandardValidatorSpec("vat_id", Map.of("country", "ES")))
                        .build())
        .addFieldGroup("totals",
                FieldSpec.builder("subtotal").type(FieldType.NUMBER).required(true).minimum(0.0).build(),
                FieldSpec.builder("tax_amount").type(FieldType.NUMBER).required(true).minimum(0.0).build(),
                FieldSpec.builder("total_amount").type(FieldType.NUMBER).required(true).minimum(0.0).build(),
                FieldSpec.required("currency", FieldType.STRING))
        // Repeating rows (array field):
        .addFieldGroup("line_items_block",
                FieldSpec.builder("line_items")
                        .type(FieldType.ARRAY)
                        .items(List.of(
                                FieldItem.of("description", FieldType.STRING),
                                FieldItem.of("quantity",    FieldType.NUMBER),
                                FieldItem.of("unit_price",  FieldType.NUMBER),
                                FieldItem.of("line_total",  FieldType.NUMBER)))
                        .build())
        .build();
```

Plug it straight into the request:

```java
ExtractionRequest req = ExtractionRequest.builder()
        .addDocument(DocumentInput.ofPath(Path.of("invoice.pdf")))
        .addDocSpec(invoice)
        .build();
```

> **Validator catalogue.** `StandardValidatorSpec.type()` is a free string so the SDK never gates on a stale list. Canonical names: `iban`, `bic`, `credit_card`, `phone_e164`, `vat_id`, `nif`, `nie`, `dni`, `uuid`, `date`, `date-time`, `email`, `uri`, `url`, `ipv4`, `ipv6`, `domain`, `slug`. Pass extras via `params` (e.g. `country`).

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
        .escalationThreshold(0.25)
        .escalationModel("anthropic:claude-opus-4-7")
        .build();

ExtractionRequest req = ExtractionRequest.builder()
        .addDocument(DocumentInput.ofPath(Path.of("invoice.pdf")))
        .addDocSpec(invoice)
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
                "The supplier VAT id passes the VAT_ID validator")
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
        .addDocument(DocumentInput.ofPath(Path.of("invoice.pdf")))
        .addDocSpec(invoice)
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

In the response, each rule's resolved output lives under `result.ruleResults()` (kept as a `List<Map<String, Object>>` so the SDK doesn't need a release every time the service ships a new rule output shape).

---

## 6. Asynchronous extraction with `waitForCompletion`

```java
import com.firefly.flydocs.sdk.model.*;
import java.time.Duration;

SubmitJobRequest submitReq = SubmitJobRequest.builder()
        .addDocument(DocumentInput.ofPath(Path.of("big-batch.pdf")))
        .addDocSpec(invoice)
        .callbackUrl("https://your-app.example.com/flydocs/webhook")
        .metadata("caller", "ingest-pipeline")
        .metadata("batch_id", "b-42")
        .build();

SubmitJobResponse submit = flydocs.submitJob(submitReq, "ingest-pipeline:b-42", null);
log.info("queued {}", submit.jobId());

JobStatusResponse finalStatus = flydocs.waitForCompletion(
        submit.jobId(),
        Duration.ofSeconds(2),
        Duration.ofMinutes(15));

switch (finalStatus.status()) {
    case SUCCEEDED -> {
        ExtractionResult result = flydocs.getJobResult(submit.jobId()).result();
        log.info("done: {} documents, {}ms", result.documents().size(), result.latencyMs());
    }
    case PARTIAL_SUCCEEDED -> {
        ExtractionResult result = flydocs.getJobResult(submit.jobId()).result();
        log.warn("partial: {} non-fatal errors", result.pipelineErrors().size());
    }
    default -> log.error("job did not succeed: {} {} {}",
            finalStatus.status(), finalStatus.errorCode(), finalStatus.errorMessage());
}
```

`waitForCompletion` returns the final `JobStatusResponse` no matter the outcome (success or failure) — only `TimeoutException` is thrown, and only when the deadline elapses while the worker is still mid-flight.

> **Idempotency.** Send the same `Idempotency-Key` to replay an existing submission instead of creating a duplicate job. The service indexes by key so retries are cheap.

---

## 7. Webhook delivery + signature verification

```java
import com.firefly.flydocs.sdk.webhook.WebhookVerifier;
import com.firefly.flydocs.sdk.webhook.WebhookVerificationException;
import com.firefly.flydocs.sdk.model.JobWebhookPayload;

WebhookVerifier verifier = new WebhookVerifier(System.getenv("FLYDOCS_WEBHOOK_HMAC_SECRET"));

@PostMapping(value = "/flydocs/webhook", consumes = APPLICATION_JSON_VALUE)
public ResponseEntity<Void> onWebhook(
        @RequestHeader("X-Flydocs-Signature") String signature,
        HttpEntity<byte[]> body) throws JsonProcessingException {
    try {
        verifier.verify(body.getBody(), signature);
    } catch (WebhookVerificationException e) {
        return ResponseEntity.status(HttpStatus.FORBIDDEN).build();
    }
    JobWebhookPayload payload = objectMapper.readValue(body.getBody(), JobWebhookPayload.class);
    if (payload.status() == JobStatus.SUCCEEDED && payload.result() != null) {
        // persist extracted fields, kick off downstream work, ...
    }
    return ResponseEntity.accepted().build();
}
```

**Important:** verify against the *raw* request body bytes. If you let Spring deserialise the JSON into a record first, re-encoding will change the digest and the verification will fail. `HttpEntity<byte[]>` keeps the raw bytes available.

---

## 8. Error handling — RFC 7807 problem-details

```java
import com.firefly.flydocs.sdk.error.*;

try {
    flydocs.extract(req);
} catch (FlydocsHttpException e) {
    switch (e.code()) {
        case "extraction_timeout" -> // fall back to async
                flydocs.submitJob(submitReqFrom(req));
        case "document_too_large" -> throw e;          // split / compress the file
        case "invalid_request"    -> {                 // payload describes every issue
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
| `extraction_timeout`    | 408    | Pipeline exceeded the sync ceiling. Retry via `submitJob`.       |
| `document_too_large`    | 413    | Document over `FLYDOCS_MAX_BYTES`.                               |
| `invalid_base64`        | 422    | `content_base64` failed strict parsing.                          |
| `invalid_request`       | 422    | Semantic validation found issues.                                |
| `job_not_ready`         | 409    | Job exists but the result isn't available yet.                   |
| `job_not_cancellable`   | 409    | Job has started; mid-flight cancellation isn't supported.        |
| `JOB_NOT_FOUND`         | 404    | Unknown `jobId`.                                                 |

---

## 9. Reactive usage

If you're already on Project Reactor (WebFlux, R2DBC, Kafka reactive consumers), use `FlydocsClientAsync` directly — there's no blocking wrapper cost:

```java
import com.firefly.flydocs.sdk.FlydocsClientAsync;
import reactor.core.publisher.Mono;

FlydocsClientAsync flydocs = FlydocsClientAsync.builder()
        .baseUrl("http://localhost:8400")
        .build();

Mono<ExtractionResult> result = flydocs.extract(req, "my-idempotency-key", "my-correlation-id");

result.subscribe(r ->
        log.info("model={} latency={}ms documents={}", r.model(), r.latencyMs(), r.documents().size()));
```

The reactive client also exposes a `waitForCompletion(jobId, pollInterval, timeout)` returning `Mono<JobStatusResponse>` — chain it into your reactive pipeline.

```java
Mono<JobResult> finalResult = flydocs.submitJob(submitReq)
        .flatMap(submit -> flydocs.waitForCompletion(
                submit.jobId(),
                Duration.ofSeconds(2),
                Duration.ofMinutes(10)))
        .filter(s -> s.status() == JobStatus.SUCCEEDED)
        .flatMap(s -> flydocs.getJobResult(s.jobId()));
```

---

## Further reading

- [`docs/api-reference.md`](../../docs/api-reference.md) — full HTTP wire contract.
- [`docs/pipeline.md`](../../docs/pipeline.md) — stage DAG, opt-in flags, what each stage does.
- [`docs/rule-engine.md`](../../docs/rule-engine.md) — rule semantics and DAG resolution.
- [`docs/standard-validators.md`](../../docs/standard-validators.md) — every built-in validator + parameters.
