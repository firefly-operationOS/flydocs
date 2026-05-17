# flydocs Official SDKs

Client libraries for [flydocs](https://github.com/firefly-operationOS/flydocs) — the pure-multimodal Intelligent Document Processing service from Firefly OperationOS. Each SDK wraps the same REST API with idiomatic, typed ergonomics for its language ecosystem.

| Language        | Path                          | Coordinates                                                          | Distribution                                          |
|-----------------|-------------------------------|----------------------------------------------------------------------|-------------------------------------------------------|
| **Python**      | [`sdks/python/`](./python/)   | `flydocs-sdk`                                                        | `.whl` + `.tar.gz` attached to each GitHub Release    |
| **Java / Spring Boot** | [`sdks/java/`](./java/) | `com.firefly.flydocs:flydocs-sdk`                                    | GitHub Packages (Maven)                               |

Both SDKs are versioned together with the service (`0.1.0` on this commit) and ship with the same surface:

- All eight REST endpoints (`/extract`, `/extract:validate`, `/jobs`, `/jobs/{id}`, `/jobs/{id}/result`, `/jobs/list`, `/version`, `/actuator/health/*`).
- Async-first design with a synchronous facade for non-async callers.
- Typed, immutable DTOs (Pydantic v2 / Java records).
- Typed exception hierarchy mapping the service's RFC 7807 problem-details.
- Constant-time HMAC webhook verifier for `X-Flydocs-Signature`.

## Install

### Python

The wheel is published as a GitHub Release asset on every `vX.Y.Z` tag. Install directly from the release URL with [`uv`](https://docs.astral.sh/uv/):

```bash
uv add https://github.com/firefly-operationOS/flydocs/releases/download/v0.1.0/flydocs_sdk-0.1.0-py3-none-any.whl
```

…or pin it in your `pyproject.toml`:

```toml
[project]
dependencies = ["flydocs-sdk"]

[tool.uv.sources]
flydocs-sdk = { url = "https://github.com/firefly-operationOS/flydocs/releases/download/v0.1.0/flydocs_sdk-0.1.0-py3-none-any.whl" }
```

```python
from flydocs_sdk import FlydocsClient, DocumentInput, ExtractionRequest

with FlydocsClient("http://localhost:8400") as flydocs:
    result = flydocs.extract(
        ExtractionRequest(
            documents=[DocumentInput.from_path("invoice.pdf")],
            docs=[{"docType": {"documentType": "invoice"}}],
        )
    )
```

Full quickstart → [sdks/python/README.md](./python/README.md).

### Java / Spring Boot

Add the GitHub Packages registry to your `~/.m2/settings.xml`:

```xml
<servers>
  <server>
    <id>github</id>
    <username>YOUR_GITHUB_USER</username>
    <password>YOUR_GITHUB_PAT_WITH_READ_PACKAGES</password>
  </server>
</servers>
```

…and the repository to your `pom.xml`:

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

```java
FlydocsClient flydocs = FlydocsClient.builder()
        .baseUrl("http://localhost:8400")
        .build();

ExtractionResult result = flydocs.extract(
        ExtractionRequest.of(
                List.of(DocumentInput.ofPath(Path.of("invoice.pdf"))),
                List.of(Map.of("docType", Map.of("documentType", "invoice")))));
```

Full quickstart → [sdks/java/README.md](./java/README.md).

## Release process

Both SDKs publish from the same workflow: `.github/workflows/publish-sdks.yaml`. The trigger is a SemVer tag push on the main repo (`vX.Y.Z`). The job:

1. Reads the version from the tag and stamps it into `pom.xml` / `pyproject.toml` / `_version.py`.
2. Builds the Java SDK (jar + sources + javadoc) and `mvn deploy`s to GitHub Packages.
3. Builds the Python SDK with `uv build` (sdist + wheel) and attaches both to the GitHub Release. Consumers install the wheel directly from the release URL with `uv add` — no PyPI publish.

Every pull request runs the `SDK Python` and `SDK Java` jobs in `pr-gate.yaml` so SDK regressions block merges.

## Local development

```bash
# Python
cd sdks/python && uv sync --extra dev && uv run pytest -q

# Java
cd sdks/java && mvn verify
```

## License

Apache-2.0 — see [LICENSE](../LICENSE).
