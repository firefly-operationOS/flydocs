# Post-extraction transformations

The `transform` pipeline stage applies caller-declared
transformations to the extracted field groups **after** every other
LLM stage (extract, judge, judge_escalation) and **before** rules /
assemble. It lets you push deduplication, normalisation, role
classification, language translation and any other post-processing
that operates on extracted data into the IDP itself — rather than
re-implementing it in every consumer.

Two transformation types ship in-tree. Adding more types is a
single-line union extension plus a new branch in
`TransformationEngine`; the public API does not change.

| Type                  | Cost                    | When to use                                                                                                                   |
| --------------------- | ----------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `entity_resolution`   | Free, ms-scale          | Deduplicate rows that refer to the same entity. Bridges accent variants, partial names, formatting differences in identifiers. |
| `llm`                 | One LLM call per group  | Anything the declarative types cannot express: role buckets, summarisation, free-text normalisation, schema migration.        |

## Enabling the stage

```json
{
  "options": {
    "stages": { "transform": true },
    "transformations": [ /* see below */ ]
  }
}
```

`transformations` is **always a list** and is applied in declared
order, so you can chain transformations against the same target — a
common pattern is `entity_resolution` first (cheap, deterministic)
followed by an `llm` step that operates on the deduped survivors.
The list can be empty: the stage is silently a no-op even with the
toggle on. Failures of individual transformations are caught by the
engine and logged; the surrounding pipeline never fails because one
transformation misbehaved.

### Chaining example

```json
{
  "options": {
    "stages": { "transform": true },
    "transformations": [
      {
        "type": "entity_resolution",
        "target_group": "personas",
        "match_by": ["dni", "nombre"],
        "scope": "request"
      },
      {
        "type": "llm",
        "target_group": "personas",
        "intention": "Classify each cargo into a closed taxonomy."
      }
    ]
  }
}
```

The LLM in the second entry sees the *deduped* rows produced by the
first one — not the originals.

## Scope: per-task vs. per-request

Every transformation declares a `scope`:

- `task` *(default)* — runs once per `(segment, DocSpec)` task and
  mutates that task's groups in place. Right for single-document
  transformations.
- `request` — concatenates the matching `target_group` across every
  task in the request, applies the transformation once over the
  consolidated rows, and emits the result as a new entry under
  `result.request_transformations`. Per-task groups are left
  untouched. Right for **cross-document** entity resolution — the same
  person mentioned in five deeds collapses into a single canonical row.

## `entity_resolution` — declarative dedup

Deterministic two-phase matcher:

1. **DNI / identifier match.** Rows whose normalised value of a given
   field (`dni`, `cif`, …) collide are merged unconditionally. The
   normaliser strips formatting (`07.549.861-L → 07549861L`) so
   document-to-document variants line up.
2. **Name-variant match.** Rows that lack a DNI fall back to NFKD-fold
   + token-subset matching. Two rows match when one name's token set
   is a subset of the other's AND they share at least
   `min_shared_tokens` tokens. The token floor (default `2`) blocks
   collapsing strangers who happen to share a single first name.

Canonical-row selection picks the most complete value per sub-field —
longest string wins for names; first non-empty wins for other types.

### Example

```json
{
  "options": {
    "stages": { "transform": true },
    "transformations": [
      {
        "type": "entity_resolution",
        "target_group": "personas",
        "match_by": ["dni", "nombre"],
        "min_shared_tokens": 2,
        "scope": "request"
      }
    ]
  }
}
```

Given personas across multiple deeds:

| nombre                          | dni          |
| ------------------------------- | ------------ |
| Andrés Contreras                |              |
| Andres Contreras Guillen        |              |
| Joaquín Sevilla                 | 07549861L    |
| Joaquín Sevilla Rodríguez       | 07.549.861-L |

→ `result.request_transformations[0].fieldGroupFields[0].fieldValueFound` will be:

| nombre                          | dni          |
| ------------------------------- | ------------ |
| Andres Contreras Guillen        |              |
| Joaquín Sevilla Rodríguez       | 07549861L    |

### Configuration reference

| Field                | Type               | Notes                                                                                                                              |
| -------------------- | ------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| `target_group`       | `string`           | `fieldGroupName` the transformation operates on. No-op if no such group is found in the task.                                      |
| `match_by`           | `list[string]`     | Field names to consider for matching, in priority order. The DNI-style field comes first; the name field is the fallback.          |
| `min_shared_tokens`  | `int` (default 2)  | Minimum shared tokens for a name-variant match. `1` is rarely safe; `2` bridges accent + partial-name variants without false merges. |
| `output_group`       | `string \| null`   | `null` = mutate the original group in place. Set a name to keep the original AND append the deduped view as a new group.           |
| `scope`              | `"task" \| "request"` (default `"task"`) | See [scope section](#scope-per-task-vs-per-request) above.                                                                         |

## `llm` — free-form transformation

The escape hatch. Caller supplies a one-sentence `intention`; the
engine renders a focused prompt against the target group's rows and
expects the LLM to return rows in the same shape. The response
replaces (or, with `output_group`, augments) the original group.

Use this for:

- **Role classification.** "Map each cargo to a closed bucket
  `{administrador_unico, consejero, apoderado, otros}`."
- **Language translation.** "Translate every value to English while
  preserving keys."
- **Schema migration.** "Rename `participacion` to `equity_pct` and
  emit a numeric percent."
- **Anonymisation.** "Replace each `nombre` with a stable token of the
  form `PERSON_NNN`."
- **Summarisation.** "Collapse the list into one summary row per
  distinct `entity_cif`."

### Example

```json
{
  "options": {
    "stages": { "transform": true },
    "transformations": [
      {
        "type": "llm",
        "target_group": "personas",
        "intention": "Normalize each cargo to a closed taxonomy: administrador_unico, consejero, apoderado, otros. Keep all other fields untouched.",
        "scope": "task"
      }
    ]
  }
}
```

### Configuration reference

| Field          | Type             | Notes                                                                                                                                         |
| -------------- | ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `target_group` | `string`         | Source `fieldGroupName`.                                                                                                                      |
| `intention`    | `string`         | One-sentence goal in any language. The LLM is prompt-engineered to be conservative — when in doubt it preserves the input.                    |
| `prompt_id`    | `string \| null` | Optional named prompt template id from the catalog. When omitted, the default `transform` prompt renders the intention into a generic shell.  |
| `output_group` | `string \| null` | Same semantics as the declarative type.                                                                                                       |
| `scope`        | enum             | Same as above.                                                                                                                                |

### Output contract

The LLM is instructed to emit `{ "rows": [...] }` where each row is a
JSON object whose keys match the input row's sub-field names (unless
the intention explicitly asks to add, rename or remove keys). The
engine materialises each returned row back into an `ExtractedField`
with sub-fields, preserving page anchors and bbox metadata from the
original row when key names match.

### Cost & latency

Each LLM transformation is one structured-output call against the
default model. Token usage is included in the request's
`usage.breakdown` under `transform.{transformation_id[:8]}`. Default
timeout per call is 600 s (override with
`FLYDOCS_TRANSFORM_TIMEOUT_S`).

## Adding a new declarative type

The DTO uses a Pydantic discriminated union keyed on `type`. A new
declarative transformation is three steps:

1. Add a Pydantic model under `interfaces/dtos/transformation.py`
   with a unique `type: Literal[...]` discriminator and the fields
   the caller will populate.
2. Add the new model to the `Transformation` union at the bottom of
   the same file.
3. Implement a new transformer service under
   `core/services/transformations/` and add a branch to
   `TransformationEngine._dispatch`.

No changes to the orchestrator or the public API are required.

## Where it sits in the pipeline

```
... → judge → judge_escalation → transform → rules → assemble
```

The placement is intentional:

- **After judge** so transformations operate on *graded* data — you
  can route only PASS-graded rows through the LLM transformer by
  pre-filtering them in your transformation prompt.
- **Before rules** so the business-rule DAG can branch on the
  transformed entities.
- **Before assemble** so the final `ExtractionResult` reflects the
  transformations.

## See also

- [`docs/pipeline.md`](pipeline.md) — full stage table and DAG
  construction.
- [`src/flydocs/interfaces/dtos/transformation.py`](../src/flydocs/interfaces/dtos/transformation.py)
  — DTO source.
- [`src/flydocs/core/services/transformations/`](../src/flydocs/core/services/transformations/)
  — implementation.
- [`tests/unit/test_entity_resolution_transformer.py`](../tests/unit/test_entity_resolution_transformer.py)
  — declarative tests.
- [`tests/unit/test_transformation_engine.py`](../tests/unit/test_transformation_engine.py)
  — dispatcher + scope tests.
