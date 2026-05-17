# flydocs — Quickstart

Zero to your first extracted invoice in **five minutes**. HTTP-only — no SDK required, no API keys.

> **Already on Python or Java?** Skip the curl tour and jump straight to the SDK quickstart:
> - **Python**: [`sdks/python/QUICKSTART.md`](sdks/python/QUICKSTART.md)
> - **Java / Spring Boot**: [`sdks/java/QUICKSTART.md`](sdks/java/QUICKSTART.md)

---

## 1. Run flydocs locally (1 min)

The repo ships with a docker-compose stack that brings up the service, a Postgres for jobs, and a mock LLM so you don't need any provider credentials:

```bash
git clone https://github.com/firefly-operationOS/flydocs.git
cd flydocs
task docker:up:test          # serves http://localhost:8400 backed by a mock LLM
```

While it boots, verify the readiness probe:

```bash
curl http://localhost:8400/actuator/health/readiness
# {"status":"UP","components":{"database_health":...,"eda_health":...}}
```

## 2. Make your first extraction (2 min)

```bash
# 1. Base64-encode any PDF / PNG / DOCX you have at hand.
B64=$(base64 < invoice.pdf | tr -d '\n')

# 2. POST a minimal ExtractionRequest. ``docs[]`` declares what to extract;
#    ``documents[]`` carries the file. Everything else has sensible defaults.
curl -sS http://localhost:8400/api/v1/extract \
  -H 'Content-Type: application/json' \
  -d @- <<JSON | jq
{
  "documents": [
    { "filename": "invoice.pdf", "content_base64": "$B64" }
  ],
  "docs": [
    {
      "docType": { "documentType": "invoice" },
      "fieldGroups": [
        {
          "fieldGroupName": "totals",
          "fieldGroupFields": [
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

You'll get back an `ExtractionResult` whose `documents[*].fields[*].fieldGroupFields[*]` carries `value`, `confidence`, and a normalised `bbox`:

```jsonc
{
  "request_id": "…",
  "model": "openai:flydocs-mock",
  "latency_ms": 412,
  "documents": [
    {
      "document_type": "invoice",
      "fields": [
        {
          "fieldGroupName": "totals",
          "fieldGroupFields": [
            {
              "name": "total_amount", "value": 1234.56, "confidence": 0.97,
              "bbox": { "page": 1, "x_min": 0.61, "y_min": 0.83, "x_max": 0.79, "y_max": 0.86,
                        "source": "llm" }
            },
            { "name": "currency", "value": "EUR", "confidence": 0.99, "bbox": { … } }
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
| **Run as an async job** with callbacks (`Idempotency-Key`, `callback_url`)   | [`docs/payload-reference.md`](docs/payload-reference.md) § 10       |
| **Verify webhook signatures** on the receiver                                  | [`docs/payload-reference.md`](docs/payload-reference.md) § 11       |
| **Branch on the RFC 7807 error catalogue**                                    | [`docs/payload-reference.md`](docs/payload-reference.md) § 12       |
| **Deploy** to your cluster (multi-arch image, Postgres, Redis, env knobs)    | [`docs/deployment.md`](docs/deployment.md)                          |
| **Understand the pipeline DAG** internals (timeouts, concurrency, cost)      | [`docs/pipeline.md`](docs/pipeline.md)                              |
| **See the full HTTP wire contract** (every endpoint, every DTO)              | [`docs/api-reference.md`](docs/api-reference.md)                    |
| **Call from Python** (typed models, async-first)                              | [`sdks/python/QUICKSTART.md`](sdks/python/QUICKSTART.md)             |
| **Call from Java / Spring Boot** (records + WebClient)                        | [`sdks/java/QUICKSTART.md`](sdks/java/QUICKSTART.md)                 |

## Troubleshooting

| Symptom                                                                      | Likely cause                                                              |
|-------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| `curl: (7) Failed to connect to localhost port 8400`                          | Service not up yet. `docker compose ps` and check `task docker:logs`.     |
| `400 Bad Request` / `422 invalid_base64`                                      | `content_base64` not strict base64 (e.g. literal newlines). Use `base64 \| tr -d '\\n'`. |
| `413 document_too_large`                                                       | File over `FLYDOCS_MAX_BYTES`. Split or compress.                          |
| `408 extraction_timeout`                                                        | Pipeline exceeded the sync ceiling. Retry through `POST /api/v1/jobs`.    |
| `422 invalid_request` with a list of `errors`                                  | Semantic validator caught an issue (rule references unknown field, etc.). Each error has `path` and `message`. |

More: [`docs/troubleshooting.md`](docs/troubleshooting.md).
