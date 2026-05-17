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

<dependency>
  <groupId>com.firefly.flydocs</groupId>
  <artifactId>flydocs-sdk</artifactId>
  <version>26.05.01</version>
</dependency>
```

Requires **Java 25** and pairs with **Spring Boot 3.5.x** managed dependencies
(it works fine outside Spring too — the only transitive runtime requirement is
`spring-webflux` + Jackson).

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

        // 4. Read the response. Each ExtractedDocument has field groups, each
        //    with extracted fields carrying value / confidence / bbox.
        System.out.printf("model=%s   latency=%dms%n",
                result.model(), result.latencyMs());
        result.documents().forEach(doc ->
                doc.fields().forEach(group ->
                        group.fieldGroupFields().forEach(field ->
                                System.out.printf("  %15s = %-20s  conf=%.2f%n",
                                        field.name(), field.value(), field.confidence()))));
    }
}
```

```
model=anthropic:claude-sonnet-4-6   latency=412ms
   total_amount = 1234.56               conf=0.97
       currency = EUR                   conf=0.99
```

That's it — you've extracted structured data from a document.

---

## What next

- **[TUTORIAL.md](./TUTORIAL.md)** — the full payload composition reference:
  every field, every option, every variant, with constraints and worked
  examples (typed schemas, rules, transformations, async jobs with
  `waitForCompletion`, webhook verification, error handling).
- **[examples/](./examples/)** — runnable Spring Boot snippets.
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
