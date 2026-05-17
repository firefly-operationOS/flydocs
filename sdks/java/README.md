# flydocs Java SDK

Official Java/Spring Boot client for [flydocs](https://github.com/firefly-operationOS/flydocs) — the pure-multimodal Intelligent Document Processing service from Firefly OperationOS.

- **Java 25** toolchain (compiled to release 25).
- **Spring Boot 3.5.x** managed dependencies — drops cleanly into any Boot 3.5 app.
- **Reactive WebClient** with a blocking `FlydocsClient` facade.
- **Records** for every DTO, immutable + null-tolerant.
- **Typed errors** mapping the service's RFC 7807 problem-details.
- **HMAC webhook verifier** with constant-time comparison.

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

<dependency>
  <groupId>com.firefly.flydocs</groupId>
  <artifactId>flydocs-sdk</artifactId>
  <version>0.1.0</version>
</dependency>
```

## Quickstart — blocking

```java
import com.firefly.flydocs.sdk.FlydocsClient;
import com.firefly.flydocs.sdk.model.*;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

FlydocsClient flydocs = FlydocsClient.builder()
        .baseUrl("http://localhost:8400")
        .build();

ExtractionResult result = flydocs.extract(
        ExtractionRequest.of(
                List.of(DocumentInput.ofPath(Path.of("invoice.pdf"))),
                List.of(Map.of(
                        "docType", Map.of("documentType", "invoice"),
                        "groups",  List.of(Map.of(
                                "fieldGroupName", "totals",
                                "fieldGroupFields", List.of(
                                        Map.of("name", "total_amount", "type", "number"),
                                        Map.of("name", "currency",      "type", "string"))))))));

System.out.printf("model=%s, latency=%dms%n", result.model(), result.latencyMs());
```

## Quickstart — reactive

```java
import com.firefly.flydocs.sdk.FlydocsClientAsync;
import com.firefly.flydocs.sdk.model.*;

FlydocsClientAsync flydocs = FlydocsClientAsync.builder()
        .baseUrl("http://localhost:8400")
        .build();

flydocs.submitJob(submitRequest, "my-app:invoice:42", null)
        .doOnNext(r -> log.info("queued {}", r.jobId()))
        .flatMap(r -> Mono.defer(() -> flydocs.getJob(r.jobId()))
                .repeatWhen(f -> f.delayElements(Duration.ofSeconds(2)))
                .filter(JobStatusResponse::isTerminal)
                .next())
        .flatMap(status -> status.status() == JobStatus.SUCCEEDED
                ? flydocs.getJobResult(status.jobId())
                : Mono.error(new IllegalStateException("job failed: " + status.errorCode())))
        .subscribe(jobResult -> log.info("got {} documents", jobResult.result().documents().size()));
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
| `version()`                           | `GET  /api/v1/version`                    | `VersionInfo`          |
| `health(probe)`                       | `GET  /actuator/health/{probe}`           | `Map<String, Object>`  |

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

## Build + test locally

```bash
cd sdks/java
mvn verify
```

## License

Apache-2.0. Copyright © 2026 Firefly Software Solutions Inc.
