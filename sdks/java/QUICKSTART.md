# flydocs Java SDK — Quickstart

The fastest path from zero to your first extracted invoice. Five minutes, end to end.

---

## 1. Install (30 s)

The artifact is published to **GitHub Packages**. Add server credentials to
`~/.m2/settings.xml` (the token only needs `read:packages`):

```xml
<servers>
  <server>
    <id>github</id>
    <username>YOUR_GITHUB_USER</username>
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

Requires **Java 25** and pairs with **Spring Boot 3.5.x** managed dependencies
(the plain SDK works fine outside Spring too — the only transitive runtime
requirement is `spring-webflux` + Jackson). The starter wires
`FlydocsClient` / `FlydocsClientAsync` (and an optional `WebhookVerifier`)
into the application context from `flydocs.*` properties.

## 2. Spin up a local flydocs (1 min)

From the flydocs repo root:

```bash
task docker:up:test     # serves http://localhost:8400 backed by a mock LLM
```

If you already have a running flydocs deployment, point `baseUrl` at it and
skip this step.

## 3. Extract (3 min)

```java
import com.firefly.flydocs.sdk.FlydocsClientAsync;
import com.firefly.flydocs.sdk.model.*;
import java.nio.file.Path;
import java.time.Duration;

public class Quickstart {

    public static void main(String[] args) {
        // 1. Describe what you want extracted. The DocSpec carries the field
        //    schema; the FieldGroup bundles related fields under one name.
        DocSpec invoice = DocSpec.builder("invoice")
                .addFieldGroup("totals",
                        FieldSpec.required("total_amount", FieldType.NUMBER),
                        FieldSpec.required("currency",     FieldType.STRING))
                .build();

        // 2. Build the request — one or more files + one or more DocSpecs.
        ExtractionRequest request = ExtractionRequest.builder()
                .addDocument(DocumentInput.ofPath(Path.of("invoice.pdf")))
                .addDocSpec(invoice)
                .build();

        // 3. Call the service. FlydocsClientAsync is the primary integration
        //    surface; it's reactive (Project Reactor) and non-blocking.
        FlydocsClientAsync flydocs = FlydocsClientAsync.builder()
                .baseUrl("http://localhost:8400")
                .build();

        ExtractionResult result = flydocs.extract(request)
                .block(Duration.ofSeconds(60));

        // 4. Read the response. ExtractionResult's per-document shape is
        //    intentionally typed as List<Map<String,Object>> so the SDK
        //    keeps working when the service adds new attributes without a
        //    coordinated release. Pull values by key for now; switch to
        //    your own typed mapping when the schema is settled.
        System.out.printf("model=%s   latency=%dms%n",
                result.model(), result.latencyMs());
        for (var doc : result.documents()) {
            System.out.printf("  doc[type=%s] keys=%s%n",
                    doc.getOrDefault("document_type", "?"),
                    doc.keySet());
        }
    }
}
```

```
model=anthropic:claude-sonnet-4-6   latency=412ms
  doc[type=invoice] keys=[document_type, pages, fields, source_file]
```

That's it — you've extracted structured data from a document.

---

## Quickstart — Spring Boot starter

If you're on Boot 3.5.x, swap the plain SDK for the starter and let
the autoconfig wire everything from properties:

```yaml
# application.yaml
flydocs:
  base-url: http://localhost:8400
  timeout: 60s
  max-attempts: 3                              # retry transient 5xx
  webhook:
    hmac-secret: ${FLYDOCS_WEBHOOK_HMAC_SECRET}  # optional
```

```java
@RestController
class Controller {
  private final FlydocsClientAsync flydocs;       // autowired
  Controller(FlydocsClientAsync flydocs) { this.flydocs = flydocs; }

  @PostMapping("/extract")
  public Mono<ExtractionResult> extract(@RequestBody ExtractionRequest req) {
    return flydocs.extract(req);
  }
}
```

The starter publishes `FlydocsClientAsync`, `FlydocsClient`, and (when the
HMAC secret is set) `WebhookVerifier`. Both clients are `AutoCloseable`
and Spring releases the Netty pool on context shutdown.

---

## What next

- **[TUTORIAL.md](./TUTORIAL.md)** — the full payload composition reference:
  every field, every option, every variant, with constraints and worked
  examples (typed schemas, rules, transformations, async jobs with
  `waitForCompletion`, webhook verification, error handling).
- **[`flydocs-examples/`](./flydocs-examples/)** — six runnable example
  classes, 1:1 with the Python SDK's examples.
- **[README.md](./README.md)** — feature matrix, API surface table, error model.

## Need a blocking API?

If you can't take a reactive dependency:

```java
import com.firefly.flydocs.sdk.FlydocsClient;

FlydocsClient flydocs = FlydocsClient.builder()
        .baseUrl("http://localhost:8400")
        .build();

ExtractionResult result = flydocs.extract(request);
```

`FlydocsClient` mirrors `FlydocsClientAsync` method-for-method, just without
the reactive plumbing. Prefer the async client whenever you can — it composes
cleanly with timeouts, retries, and `waitForCompletion` polling.
