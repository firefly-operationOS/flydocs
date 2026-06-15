# flydocs — Quickstart

Zero to your first extracted invoice in **five minutes**. HTTP-only — no SDK required, no API keys.

> **Migrating from v0?** See [`docs/migration-v0-to-v1.md`](docs/migration-v0-to-v1.md) — every old key, every renamed endpoint, every enum value, side by side with the v1 equivalent.

> **Already on Python or Java?** Skip the curl tour and jump straight to the SDK quickstart:
> - **Python**: [`sdks/python/QUICKSTART.md`](sdks/python/QUICKSTART.md)
> - **Java / Spring Boot**: [`sdks/java/QUICKSTART.md`](sdks/java/QUICKSTART.md)

---

## 1. Run flydocs locally (1 min)

The repo ships with a docker-compose stack that brings up the service, a Postgres for the `extractions` table, and a mock LLM so you don't need any provider credentials:

```bash
git clone https://github.com/firefly-operationOS/flydocs.git
cd flydocs
task docker:up:test          # serves http://localhost:8080 backed by a mock LLM
```

While it boots, verify the readiness probe:

```bash
curl http://localhost:9090/actuator/health/readiness
# {"status":"UP","components":{"database_health":...,"eda_health":...}}
```

## 2. Make your first extraction (2 min)

```bash
# 1. Base64-encode any PDF / PNG / DOCX you have at hand.
B64=$(base64 < invoice.pdf | tr -d '\n')

# 2. POST a minimal ExtractionRequest. ``document_types[]`` declares what to extract;
#    ``files[]`` carries the binary. Everything else has sensible defaults.
curl -sS http://localhost:8080/api/v1/extract \
  -H 'Content-Type: application/json' \
  -d @- <<JSON | jq
{
  "files": [
    { "filename": "invoice.pdf", "content_base64": "$B64" }
  ],
  "document_types": [
    {
      "id": "invoice",
      "field_groups": [
        {
          "name": "totals",
          "fields": [
            { "name": "total_amount", "type": "number", "required": true },
            { "name": "currency",     "type": "string", "required": true }
          ]
        }
      ]
    }
  ]
}
JSON
```

You'll get back an `ExtractionResult` whose `documents[*].field_groups[*].fields[*]` carries `name`, `value`, `confidence`, and a normalised `bbox`:

```jsonc
{
  "id":     "ext_01HEM...",
  "status": "success",
  "pipeline": {
    "model":      "openai:flydocs-mock",
    "latency_ms": 412
  },
  "documents": [
    {
      "type": "invoice",
      "field_groups": [
        {
          "name": "totals",
          "fields": [
            {
              "name": "total_amount", "value": 1234.56, "confidence": 0.97,
              "bbox": { "xmin": 0.61, "ymin": 0.83, "xmax": 0.79, "ymax": 0.86,
                        "source": "llm", "quality": "good", "quality_score": 0.92,
                        "refinement_confidence": null }
            },
            { "name": "currency", "value": "EUR", "confidence": 0.99, "bbox": { /* ... */ } }
          ]
        }
      ]
    }
  ]
}
```

That's the **mandatory pipeline** — multimodal extract + bbox. Everything else (validation, business rules, judge re-eval, authenticity, OCR-grounded bbox refinement, transformations) is opt-in via `options.stages`.

## 3. Where to go next (2 min)

| You want to…                                                                 | Read                                                                 |
|-------------------------------------------------------------------------------|----------------------------------------------------------------------|
| **Compose a realistic schema** (field types, formats, validators, arrays)    | [`docs/payload-reference.md`](docs/payload-reference.md) §§ 4–6     |
| **Tune the pipeline** (which stages to enable, model selection, escalation)  | [`docs/payload-reference.md`](docs/payload-reference.md) § 7        |
| **Add business rules** over extracted fields + validator outcomes            | [`docs/payload-reference.md`](docs/payload-reference.md) § 8        |
| **Run as an async extraction** with callbacks (`Idempotency-Key`, `callback_url`) | [`docs/payload-reference.md`](docs/payload-reference.md) § 10  |
| **Verify webhook signatures** on the receiver                                  | [`docs/payload-reference.md`](docs/payload-reference.md) § 11       |
| **Branch on the RFC 7807 error catalogue**                                    | [`docs/payload-reference.md`](docs/payload-reference.md) § 13       |
| **Migrate from v0**                                                            | [`docs/migration-v0-to-v1.md`](docs/migration-v0-to-v1.md)          |
| **Deploy** to your cluster (multi-arch image, Postgres, Redis, env knobs)    | [`docs/deployment.md`](docs/deployment.md)                          |
| **Understand the pipeline DAG** internals (timeouts, concurrency, cost)      | [`docs/pipeline.md`](docs/pipeline.md)                              |
| **See the full HTTP wire contract** (every endpoint, every DTO)              | [`docs/api-reference.md`](docs/api-reference.md)                    |
| **Call from Python** (typed models, async-first)                              | [`sdks/python/QUICKSTART.md`](sdks/python/QUICKSTART.md)             |
| **Call from Java / Spring Boot** (records + WebClient)                        | [`sdks/java/QUICKSTART.md`](sdks/java/QUICKSTART.md)                 |

## Troubleshooting

| Symptom                                                                      | Likely cause                                                              |
|-------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| `curl: (7) Failed to connect to localhost port 8080`                          | Service not up yet. `docker compose ps` and check `task docker:logs`.     |
| `400 Bad Request` / `422 invalid_base64`                                      | `content_base64` not strict base64 (e.g. literal newlines). Use `base64 \| tr -d '\\n'`. |
| `413 file_too_large`                                                            | File over `FLYDOCS_MAX_BYTES`. Split or compress.                          |
| `408 timeout`                                                                    | Pipeline exceeded the sync ceiling. Retry through `POST /api/v1/extractions`. |
| `422 validation_failed` with a list of `errors`                                | Semantic validator caught an issue (rule references unknown field, etc.). Each error has `path` and `message`. |

More: [`docs/troubleshooting.md`](docs/troubleshooting.md).
