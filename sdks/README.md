# flydocs Official SDKs

Client libraries for [flydocs](https://github.com/firefly-operationOS/flydocs) — the pure-multimodal Intelligent Document Processing service from Firefly OperationOS. Each SDK wraps the same REST API with idiomatic, typed ergonomics for its language ecosystem.

| Language               | Path                          | Coordinates                                                          | Distribution                                          |
|------------------------|-------------------------------|----------------------------------------------------------------------|-------------------------------------------------------|
| **Python**             | [`sdks/python/`](./python/)   | `flydocs-sdk`                                                        | `.whl` + `.tar.gz` attached to each GitHub Release    |
| **Java / Spring Boot** | [`sdks/java/`](./java/)       | `com.firefly.flydocs:flydocs-sdk`                                    | GitHub Packages (Maven)                               |

Both SDKs are versioned together with the service (`26.6.0` on this commit) and ship with the same v1 contract surface:

- Every REST endpoint (`/extract`, `/extract:validate`, `/extractions`, `/extractions/{id}`, `/extractions/{id}/result`, `/extractions` (list), `/version`, `/actuator/health/*`).
- Async-first design with a synchronous facade for non-async callers.
- Typed, immutable DTOs (Pydantic v2 / Java records).
- Typed exception hierarchy mapping the service's RFC 7807 problem-details (`not_found`, `not_ready`, `timeout`, `file_too_large`, `validation_failed`, …).
- Constant-time HMAC `WebhookVerifier` for `X-Flydocs-Signature` — returns a typed `WebhookEnvelope` / `EventEnvelope`.

> **Coming from v0?** Both SDKs were rewritten in lockstep with the v1 contract. See [`docs/migration-v0-to-v1.md`](../docs/migration-v0-to-v1.md) for the full rename table and § 8 of that guide for side-by-side Python and Java upgrade snippets.

## Per-language quickstarts

- **Python** — [`sdks/python/README.md`](./python/README.md) · [`sdks/python/QUICKSTART.md`](./python/QUICKSTART.md) · [`sdks/python/TUTORIAL.md`](./python/TUTORIAL.md)
- **Java / Spring Boot** — [`sdks/java/README.md`](./java/README.md) · [`sdks/java/QUICKSTART.md`](./java/QUICKSTART.md) · [`sdks/java/TUTORIAL.md`](./java/TUTORIAL.md)

## Release process

Both SDKs use **CalVer `YY.M.PP`** (PEP 440 normalises `26.06.00` → `26.6.0` for the Python wheel name). Both publish from the same workflow, `.github/workflows/publish-sdks.yaml`, on `v*.*.*` tag push.

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
