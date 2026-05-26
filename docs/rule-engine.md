# The business rule engine

Rules turn extracted data into **decisions** the surrounding workflow
can act on. Should we auto-approve? Send to manual review? Reject?
Escalate to fraud? These are the questions a hand-coded if/else block
ends up answering in most extraction pipelines. flydocs lets you
declare them as data and resolve them with an LLM evaluator that walks
your dependency graph.

> **What this doc covers:** rule shape, DAG mechanics, predicate
> design, cycle detection, output coercion. **When to read it:** while
> declaring `rules[]` on a request. **Where else to look:**
> - HTTP shape: [`api-reference.md`](api-reference.md) +
>   [`payload-reference.md § 8`](payload-reference.md#8-rules--business-rules-over-extracted-fields).
> - Pipeline integration: [`pipeline.md`](pipeline.md) (`rules` stage).
> - Migrating from v0: [`migration-v0-to-v1.md`](migration-v0-to-v1.md).

---

## 1. Anatomy of a rule

```jsonc
{
  "id": "kyc_complete",                              // unique, stable
  "predicate": "All identity fields are populated AND nif is valid.",
  "parents": [
    {"kind": "field",     "document_type": "passport",
     "fields": ["full_name", "nif"]},
    {"kind": "validator", "document_type": "passport",
     "validator": "photo_present"}
  ],
  "output": {
    "type": "boolean",                               // boolean | string | number
    "valid_outputs": ["true", "false"]               // optional closed set
  }
}
```

| Field                    | Meaning                                                                                                |
| ------------------------ | ------------------------------------------------------------------------------------------------------ |
| `id`                     | Stable identifier. Other rules reference it via `RuleRuleParent.rule`.                                 |
| `predicate`              | Natural-language decision statement. The LLM evaluates it.                                              |
| `parents`                | Declared dependencies. Three kinds (discriminator: `kind`):                                            |
|                          | • `"field"`     — fields the rule reads from a document type.                                          |
|                          | • `"validator"` — validator outcome the rule reads.                                                    |
|                          | • `"rule"`      — another rule's id; creates the DAG edge.                                             |
| `output.type`            | `"boolean"`, `"string"`, or `"number"`. Drives the expected output shape.                              |
| `output.valid_outputs`   | Optional closed set. If the LLM returns something outside, the rule is flagged for human review.       |

---

## 2. What the engine actually does

```python
sorter = TopologicalSorter[str]()      # nodes are rule ids (hashable)
for rule in rules:
    parents = [p.rule for p in rule.parents if isinstance(p, RuleRuleParent)]
    sorter.add(rule.id, *parents)
sorter.prepare()                       # cycles -> ValueError before any LLM call

results = []
while sorter.is_active():
    level = sorter.get_ready()         # every rule whose parents are resolved
    prompt = render(active_rules=level,
                    documents_context=<fields + validators the rules read>,
                    previous_results=<prior rules' outputs the new rules read>)
    output = await agent.run(prompt)
    results += output.rule_results
    sorter.done(*level)
```

Key properties:

- **One LLM call per DAG level.** Rules that don't depend on each
  other share a prompt, which keeps the token bill (and the latency)
  proportional to the rule depth, not the rule count.
- **The prompt is scoped.** Only the fields, validator outcomes, and
  prior rule outputs declared as parents are included — the engine
  doesn't pour the whole extraction into every rule's context.
- **Cycles are rejected before any LLM call** — Python's
  `graphlib.TopologicalSorter.prepare()` raises `CycleError`, which the
  engine wraps in a `ValueError`. No chance of an infinite loop reaching
  the model.

---

## 3. A worked example — KYC

Two rules: one decides whether the document is "complete enough"; the
other escalates to manual review when the first is `false`.

```jsonc
{
  "rules": [
    {
      "id": "kyc_complete",
      "predicate": "All identity fields are populated AND nif is valid.",
      "parents": [
        {"kind": "field",     "document_type": "passport",
         "fields": ["full_name", "nif"]},
        {"kind": "validator", "document_type": "passport",
         "validator": "photo_present"}
      ],
      "output": {"type": "boolean", "valid_outputs": ["true", "false"]}
    },
    {
      "id": "needs_manual_review",
      "predicate": "Set to true when kyc_complete is false OR any field has a hard validation error.",
      "parents": [
        {"kind": "rule", "rule": "kyc_complete"}
      ],
      "output": {"type": "boolean", "valid_outputs": ["true", "false"]}
    }
  ]
}
```

Two LLM calls total:

1. **Level 1** evaluates `kyc_complete`. Context includes only the
   `full_name` and `nif` fields plus the `photo_present` validator
   outcome.
2. **Level 2** evaluates `needs_manual_review`. Context includes the
   `kyc_complete` output from level 1.

The level-2 prompt does _not_ include `full_name` or `nif` — the rule
didn't declare them as parents.

---

## 4. Real-world example — Spanish notarial deed

This is the scenario the real-LLM smoke test exercises against Claude
Opus 4-7:

```jsonc
{
  "rules": [
    {
      "id": "kyc_complete",
      "predicate": "Both otorgante_nombre and apoderado_nombre are populated, and otorgante_dni_nie and apoderado_dni_nie are populated, and fecha is populated.",
      "parents": [
        {"kind": "field", "document_type": "escritura_poderes",
         "fields": ["otorgante_nombre", "apoderado_nombre",
                    "otorgante_dni_nie", "apoderado_dni_nie", "fecha"]}
      ],
      "output": {"type": "boolean", "valid_outputs": ["true", "false"]}
    },
    {
      "id": "parties_distinct",
      "predicate": "The otorgante_nombre and apoderado_nombre refer to different individuals (case- and accent-insensitive).",
      "parents": [
        {"kind": "field", "document_type": "escritura_poderes",
         "fields": ["otorgante_nombre", "apoderado_nombre"]}
      ],
      "output": {"type": "boolean", "valid_outputs": ["true", "false"]}
    },
    {
      "id": "recent_document",
      "predicate": "The fecha is on or after 2020-01-01.",
      "parents": [
        {"kind": "field", "document_type": "escritura_poderes",
         "fields": ["fecha"]}
      ],
      "output": {"type": "boolean", "valid_outputs": ["true", "false"]}
    }
  ]
}
```

All three rules live at level 1 (no inter-rule dependencies), so the
engine resolves them in **one** LLM call. Sample output on a deed
where the grantor is also the proxy:

```
kyc_complete       → true   All five required KYC fields are populated.
parties_distinct   → false  Otorgante and apoderado are the same person (self-power).
recent_document    → true   Document date 2025-05-15 is after 2020-01-01.
```

The `parties_distinct: false` result is the rule engine catching
something a hand-coded extractor would have missed: the deed is a
self-apoderamiento where the same DNI appears on both sides.

---

## 5. Designing predicates

The predicate is **natural language** — the LLM evaluates it. Some
rules of thumb:

1. **Be explicit about the inputs.** Reference fields by name and say
   what "set" / "valid" means in the predicate text itself. The LLM
   sees the context JSON, but the predicate is the anchor.
2. **One decision per rule.** "All identity fields are present AND the
   IBAN is valid" can be one rule, but splitting it into two
   (`all_present`, `iban_valid`) makes each easier to debug and lets
   downstream rules depend on the precise sub-decision.
3. **Bound the output.** `valid_outputs` lets the engine flag a rule
   for human review when the LLM returns something off-script.
   Especially useful when `type: "string"` and the rule has only a
   handful of legal answers (`"approve"`, `"reject"`, `"investigate"`).
4. **Avoid asking the LLM to count.** If you need "all of these fields
   are populated", phrase it that way — but also know it'll be
   evaluated against the JSON context, where empty values appear as
   `null`.

---

## 6. When a rule is flagged

Each `RuleResult` carries a `human_revision` string (or `null`). The
LLM uses it to say _"I couldn't decide because X"_ or _"the input was
ambiguous"_. Treat any non-`null` `human_revision` as a signal to route
the case to a human reviewer.

The engine also marks anything outside `valid_outputs` (when
provided). Read `output` and compare it to the allowed set in your
handler code, or pre-filter at the response boundary.

---

## 7. Cycles

The engine rejects cycles before the first LLM call:

```python
ValueError: Rule graph contains a cycle: ['a', 'b', 'a']
```

This is enforced by Python's stdlib `graphlib.TopologicalSorter`. No
chance of an infinite loop reaching the model — a regression here
would surface immediately at request validation (`422
validation_failed`).

---

## 8. Limits and trade-offs

- **One LLM call per DAG level.** A rule graph with three sequential
  levels means three LLM calls. Design rules to collapse into fewer
  levels where possible.
- **The LLM is the substrate.** Rules can't compute arbitrary
  arithmetic deterministically — _"sum of line items == total"_ is
  more reliably a pure-Python validator than a rule. Use rules for
  semantic judgements that benefit from the LLM's flexibility.
- **Rules cost tokens.** Each level's prompt includes the rule
  definitions + the relevant fields / validators / prior outputs.
  Don't dump the whole extraction in every rule's context — the
  engine filters to the declared `parents` for you.
- **The output is a string.** Even `boolean` rules return `"true"` or
  `"false"` — coerce in your handler. This makes the protocol
  forward-compatible with `string` / `number` outputs that don't have
  a one-line JSON representation.
