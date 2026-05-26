# Built-in validators

The deterministic, pure-Python validators flydocs ships out of the
box. They run **after** extraction (no LLM call) and let you catch
syntactically invalid values — checksums, country-specific identifier
shapes, ISO codes, … — without writing a single regex.

> **What this doc covers:** every built-in validator, its algorithm,
> its params, its severity semantics. **When to read it:** while
> declaring `validators[]` on a `Field`. **Where else to look:**
> - Field-level shape: [`payload-reference.md § 5`](payload-reference.md#5-field--field-level-shape-and-constraints).
> - Endpoint catalogue: [`api-reference.md`](api-reference.md).
> - Migrating from v0's `standard_validators[]`: [`migration-v0-to-v1.md`](migration-v0-to-v1.md).

Declare them per `Field`:

```jsonc
{
  "name": "tax_id",
  "type": "string",
  "validators": [
    { "name": "nif" },                                   // hard error if invalid
    { "name": "nie",    "severity": "warning" },          // soft, value stays valid
    { "name": "vat_id", "params": { "country": "ES" } }
  ]
}
```

Every validator returns `None` on success or a human-readable message
on failure. The message is recorded on the field's
`validation.errors[].message`, prefixed by the validator name (e.g.
`nif: '12345678X' is not a valid Spanish NIF`).

**Severity:**

- `error` (default) — a failure flips `validation.valid` to `false`.
- `warning` — the error is recorded but `valid` stays `true`. Use for
  suggestive checks ("this *might* be a NIE — flag but don't reject").

The same field can declare multiple validators. They run independently
and accumulate their findings.

The dispatch key is **`name`** (not `type` — `type` is the v0 spelling
and is rejected on the wire).

---

## 1. Network

| `name`      | Checks                                                                 |
| ----------- | ---------------------------------------------------------------------- |
| `email`     | RFC-shaped regex (`local@domain.tld`).                                  |
| `uri` / `url` | Has a `scheme://` and a host.                                         |
| `domain`    | DNS-shaped: labels of `[A-Za-z0-9-]`, dotted, ≤ 253 chars total.        |
| `slug`      | URL-friendly: `[a-z0-9]+(-[a-z0-9]+)*`.                                |
| `ipv4`      | Parses as `ipaddress.IPv4Address`.                                     |
| `ipv6`      | Parses as `ipaddress.IPv6Address`.                                     |

## 2. Temporal

| `name`      | Checks                                                          |
| ----------- | --------------------------------------------------------------- |
| `date`      | `YYYY-MM-DD` parses via `date.fromisoformat`.                   |
| `datetime`  | ISO 8601 datetime parses via `datetime.fromisoformat`.          |
| `time`      | `HH:MM[:SS]` parses via `time.fromisoformat`.                   |
| `iso_8601`  | Alias for `datetime`.                                           |

## 3. Identifiers

| `name`      | Checks                                  |
| ----------- | --------------------------------------- |
| `uuid`      | Parses as `UUID`.                       |
| `json`      | Parses as JSON (string or list/dict).   |
| `hex_color` | `#?[0-9a-fA-F]{3,8}`.                   |

## 4. Finance

| `name`          | Checks                                                                            |
| --------------- | --------------------------------------------------------------------------------- |
| `iban`          | ISO 13616 layout + mod-97 checksum.                                               |
| `bic`           | 8 or 11 chars, ISO 9362 layout.                                                   |
| `credit_card`   | 13–19 digits + Luhn checksum.                                                     |
| `currency_code` | ISO 4217 alpha-3 (closed set of common codes).                                    |
| `amount`        | Numeric. Params: `allow_zero` (default `false`), `allow_negative` (default `false`).|

## 5. Telephony

| `name`       | Checks                                                                |
| ------------ | --------------------------------------------------------------------- |
| `phone_e164` | `\+?[1-9]\d{6,14}` after stripping spaces / dashes / parentheses.      |

## 6. Geographic

| `name`          | Checks                                                                                                                |
| --------------- | --------------------------------------------------------------------------------------------------------------------- |
| `country_code`  | ISO 3166-1 alpha-2.                                                                                                   |
| `language_code` | ISO 639-1 alpha-2.                                                                                                    |
| `postal_code`   | Country-aware. Params: `country` (ISO 3166-1 alpha-2). Built-in: ES, FR, DE, IT, GB, US, PT, NL, BE, BR; generic permissive shape otherwise. |
| `latitude`      | Float in `[-90, 90]`.                                                                                                 |
| `longitude`     | Float in `[-180, 180]`.                                                                                               |

## 7. National identifiers

| `name`            | Checks                                                                                        |
| ----------------- | --------------------------------------------------------------------------------------------- |
| `nif`             | Spanish NIF: 8 digits + control letter, with the canonical mod-23 checksum.                   |
| `nie`             | Spanish NIE: `[XYZ]` prefix + 7 digits + control letter.                                      |
| `cif`             | Spanish CIF (legacy company id): letter prefix + 7 digits + checksum digit / letter.          |
| `vat_id`          | EU VAT: per-country regex. Params: `country` (defaults to first two characters of the value). |
| `ssn`             | US SSN: `\d{3}-?\d{2}-?\d{4}`.                                                                |
| `passport_number` | Shape: `[A-Z0-9]{5,12}` (no national checksum).                                               |

---

## 8. Worked examples

### Soft-match NIF or NIE

A common pattern when a field carries either a Spanish DNI (NIF) or a
NIE — declare both as warnings so neither failing flips the field
invalid:

```jsonc
{
  "name": "tax_id",
  "type": "string",
  "validators": [
    { "name": "nif", "severity": "warning" },
    { "name": "nie", "severity": "warning" }
  ]
}
```

At least one will accept the value when it's syntactically a NIF or
NIE; the field stays `valid: true` because warnings don't flip the
flag.

### Country-aware VAT ID

```jsonc
{
  "name": "supplier_vat",
  "type": "string",
  "required": true,
  "validators": [
    { "name": "vat_id", "params": { "country": "ES" } }
  ]
}
```

### Inside an array of objects

Validators work at any depth — declare them on the inner `Field`:

```jsonc
{
  "name": "line_items",
  "type": "array",
  "items": {
    "type": "object",
    "name": "line_item",
    "fields": [
      { "name": "supplier_iban", "type": "string",
        "validators": [{ "name": "iban" }] },
      { "name": "amount",        "type": "number", "minimum": 0,
        "validators": [{ "name": "amount", "params": { "allow_zero": false } }] }
    ]
  }
}
```

---

## 9. Adding a new validator

The registry is open by design — drop in a new checker without
touching anything else.

1. **Append a member** to
   `interfaces/enums/validator.py::ValidatorType`.
2. **Implement the checker** in
   `core/services/validation/validator_registry.py`:

   ```python
   def _check_my_thing(value: Any, params: dict) -> str | None:
       if not _looks_right(value, params):
           return f"{value!r} is not a valid my-thing"
       return None
   ```

3. **Register** it in `CHECKERS`:

   ```python
   CHECKERS[ValidatorType.MY_THING] = _check_my_thing
   ```

4. **Unit test** it under `tests/unit/test_validators.py`. Cover one
   positive case and one or two negative cases that highlight the
   boundary.

That's it — the existing `FieldValidator` automatically picks it up
because it dispatches by `ValidatorType`.

---

## 10. Design notes

- **Validators are pure functions**, not classes. The registry is just
  a dict keyed by enum. There is no inheritance hierarchy because
  there's nothing for inheritance to share — each validator is one
  ~10-line function.
- **They never call the LLM.** That's deliberate: the validators are
  the deterministic anchor of the response. If a value passes its
  validators, that fact is reproducible.
- **They run in pure Python.** No allocations, no I/O, no network. A
  request with a hundred validators per field stays under a few ms
  for the whole `field_validation` stage.
- **Severity is per-validator-instance, not per-validator-type.** The
  same `nie` validator can be a hard error on one field and a warning
  on another, depending on how the caller declared it.
