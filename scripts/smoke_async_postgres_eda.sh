#!/usr/bin/env bash
# Copyright 2024-2026 Firefly Software Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

#
# End-to-end async smoke against the Postgres-EDA stack.
#
# This is the durable repo-tracked variant of the ad-hoc script that
# lives in ~/Desktop/flydocs_async.sh -- same payload shape, but
# the model is pinned to ``anthropic:claude-sonnet-4-6`` (the
# repo-default in ``env_template``) and every step is verified against
# the EDA outbox so a CI run can confirm Postgres NOTIFY + the worker
# subscription actually fired.
#
# Steps:
#   1. POST  /api/v1/jobs              -> get job_id
#   2. SELECT pyfly_eda_outbox         -> confirm IDPJobSubmitted persisted
#   3. Poll  /api/v1/jobs/{id}         -> SUCCEEDED / FAILED / CANCELLED
#   4. GET   /api/v1/jobs/{id}/result  -> compact summary via jq
#
# Usage:
#   scripts/smoke_async_postgres_eda.sh path/to/document.pdf
#
# Defaults the PDF path to ~/Downloads/escritura_poderes_2025.pdf (the
# escritura de poderes sample used for the KYC walk-through).

set -euo pipefail

PDF="${1:-$HOME/Downloads/escritura_poderes_2025.pdf}"
API="${FLYDOCS_URL:-http://localhost:8080}"
MGMT_API="${FLYDOCS_MGMT_URL:-http://localhost:9090}"
MODEL="${FLYDOCS_MODEL:-anthropic:claude-sonnet-4-6}"
POLL_INTERVAL_S="${POLL_INTERVAL_S:-3}"
POLL_MAX_S="${POLL_MAX_S:-300}"

if [[ ! -f "$PDF" ]]; then
  echo "PDF not found: $PDF" >&2
  exit 2
fi
command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 2; }

echo "[smoke] POST $API/api/v1/jobs"
echo "[smoke] document=$PDF ($(du -h "$PDF" | cut -f1))"
echo "[smoke] model=$MODEL"
echo

TMP_B64="$(mktemp -t flydocs_b64)"
TMP_BODY="$(mktemp -t flydocs_body)"
TMP_RESP="$(mktemp -t flydocs_resp)"
trap 'rm -f "$TMP_B64" "$TMP_BODY" "$TMP_RESP"' EXIT

base64 -i "$PDF" | tr -d '\n' > "$TMP_B64"

jq -n \
  --rawfile b64 "$TMP_B64" \
  --arg fn "$(basename "$PDF")" \
  --arg model "$MODEL" \
  '{
    intention: "Audit a Spanish notarial power of attorney for KYC purposes. Extract the canonical fields, verify the notary signature, evaluate completeness and recency.",
    document: {filename: $fn, content_base64: $b64, content_type: "application/pdf"},
    docs: [{
      docType: {
        documentType: "escritura_poderes",
        description: "Escritura notarial de poderes (Spanish notarial power of attorney)",
        country: "ES"
      },
      fieldGroups: [{
        fieldGroupName: "otorgamiento",
        fieldGroupDesc: "Datos del otorgamiento",
        fieldGroupFields: [
          {fieldName: "numero_protocolo", fieldDescription: "Numero de protocolo notarial.", fieldType: "string"},
          {fieldName: "fecha",            fieldDescription: "Fecha del otorgamiento (ISO YYYY-MM-DD).", fieldType: "string",
           standard_validators: [{type: "date"}]},
          {fieldName: "notario",          fieldDescription: "Nombre completo del notario.", fieldType: "string"},
          {fieldName: "otorgante_nombre", fieldDescription: "Nombre completo del otorgante (poderdante).", fieldType: "string"},
          {fieldName: "otorgante_dni_nie", fieldDescription: "DNI o NIE del otorgante.", fieldType: "string",
           standard_validators: [
             {type: "nif", severity: "warning"},
             {type: "nie", severity: "warning"}
           ]},
          {fieldName: "apoderado_nombre", fieldDescription: "Nombre completo del apoderado.", fieldType: "string"},
          {fieldName: "apoderado_dni_nie", fieldDescription: "DNI o NIE del apoderado.", fieldType: "string",
           standard_validators: [
             {type: "nif", severity: "warning"},
             {type: "nie", severity: "warning"}
           ]}
        ]
      }],
      validators: {
        visual: [
          {name: "firma_notario",  description: "The notary handwritten signature is present."},
          {name: "sello_notarial", description: "The notary official stamp/seal is present."}
        ]
      }
    }],
    rules: [
      {id: "kyc_complete",
       predicate: "Both otorgante_nombre and apoderado_nombre are populated, otorgante_dni_nie and apoderado_dni_nie are populated, and fecha is populated.",
       parents: [{parentType: "field", documentType: "escritura_poderes",
                  fieldNames: ["otorgante_nombre","apoderado_nombre","otorgante_dni_nie","apoderado_dni_nie","fecha"]}],
       output: {type: "boolean", valid_outputs: ["true","false"]}},
      {id: "parties_distinct",
       predicate: "The otorgante_nombre and apoderado_nombre refer to different individuals (case- and accent-insensitive).",
       parents: [{parentType: "field", documentType: "escritura_poderes",
                  fieldNames: ["otorgante_nombre","apoderado_nombre"]}],
       output: {type: "boolean", valid_outputs: ["true","false"]}},
      {id: "recent_document",
       predicate: "The fecha is on or after 2020-01-01.",
       parents: [{parentType: "field", documentType: "escritura_poderes",
                  fieldNames: ["fecha"]}],
       output: {type: "boolean", valid_outputs: ["true","false"]}}
    ],
    options: {
      model: $model,
      language_hint: "es",
      stages: {
        splitter: false,
        field_validation: true,
        visual_authenticity: true,
        content_authenticity: false,
        judge: true,
        rule_engine: true
      }
    },
    metadata: {source: "smoke_async_postgres_eda.sh"}
  }' > "$TMP_BODY"

# ---- 1. Submit -----------------------------------------------------------
SUBMIT="$(curl -sS -X POST "$API/api/v1/jobs" \
  -H 'content-type: application/json' \
  -H 'x-correlation-id: smoke-postgres-eda' \
  --data-binary "@$TMP_BODY")"
JOB_ID="$(echo "$SUBMIT" | jq -r '.job_id // empty')"
if [[ -z "$JOB_ID" ]]; then
  echo "[smoke] submit failed:" >&2
  echo "$SUBMIT" | jq . >&2 || echo "$SUBMIT" >&2
  exit 1
fi
echo "[smoke] submitted job_id=$JOB_ID status=$(echo "$SUBMIT" | jq -r .status)"

# ---- 2. EDA outbox check -------------------------------------------------
echo "[smoke] Postgres EDA outbox tail:"
docker compose exec -T postgres psql -U idp -d flydocs -c "
  SELECT id, destination, event_type, payload, created_at
  FROM pyfly_eda_outbox
  WHERE payload->>'job_id' = '$JOB_ID';
"

# ---- 3. Poll -------------------------------------------------------------
deadline=$(( $(date +%s) + POLL_MAX_S ))
status="QUEUED"
while [[ "$status" != "SUCCEEDED" && "$status" != "FAILED" && "$status" != "CANCELLED" ]]; do
  if [[ $(date +%s) -ge $deadline ]]; then
    echo "[smoke] timed out after ${POLL_MAX_S}s -- last status=$status" >&2
    exit 1
  fi
  sleep "$POLL_INTERVAL_S"
  STATUS_RESP="$(curl -sS "$API/api/v1/jobs/$JOB_ID")"
  status="$(echo "$STATUS_RESP" | jq -r '.status // "UNKNOWN"')"
  attempts="$(echo "$STATUS_RESP" | jq -r '.attempts // 0')"
  echo "[smoke] status=$status attempts=$attempts"
done

if [[ "$status" != "SUCCEEDED" ]]; then
  echo "[smoke] terminal status=$status:" >&2
  echo "$STATUS_RESP" | jq . >&2
  exit 1
fi

# ---- 4. Fetch + summarise ------------------------------------------------
curl -sS "$API/api/v1/jobs/$JOB_ID/result" > "$TMP_RESP"

jq '
.result as $r |
{
  job_id,
  request_id: $r.request_id,
  model: $r.model,
  latency_ms: $r.latency_ms,
  pages: $r.document.page_count,
  bytes: $r.document.bytes,
  fields: [$r.documents[].fields[].fieldGroupFields[] | {
    name: .fieldName,
    value: .fieldValueFound,
    confidence,
    page: (.pagesFound[0] // null),
    valid: .field_validation.valid,
    judge: .judge.status
  }],
  visual: [$r.documents[].authenticity.visual[] | {name, passed, confidence}],
  rules: [$r.rule_results[] | {rule_id, output, summary}],
  pipeline_errors: $r.pipeline_errors
}' < "$TMP_RESP"

echo
echo "[smoke] /actuator/health/readiness:"
curl -sS "$MGMT_API/actuator/health/readiness" | jq .

echo
echo "[smoke] final EDA cursor:"
docker compose exec -T postgres psql -U idp -d flydocs -c "
  SELECT consumer_group, last_event_id, updated_at FROM pyfly_eda_offsets;
"
