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

"""Pure-Python implementations for every :class:`ValidatorType`.

Each checker is a function ``(value: Any, params: dict) -> str | None``
that returns ``None`` on success or a human-readable error message on
failure. The :class:`FieldValidator` looks the function up by
``ValidatorType`` and runs it after the simple constraint set.
"""

from __future__ import annotations

import ipaddress
import json
import re
from datetime import date, datetime, time
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from flydocs.interfaces.enums.validator import ValidatorType

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _need_str(value: Any) -> str | tuple[None, str]:
    if not isinstance(value, str) or not value.strip():
        return None, "value must be a non-empty string"
    return value.strip()


# ---------------------------------------------------------------------------
# Network / web
# ---------------------------------------------------------------------------


def _check_email(value: Any, _: dict) -> str | None:
    v = _need_str(value)
    if isinstance(v, tuple):
        return v[1]
    if not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", v):
        return f"{value!r} is not a valid email address"
    return None


def _check_uri(value: Any, _: dict) -> str | None:
    v = _need_str(value)
    if isinstance(v, tuple):
        return v[1]
    parsed = urlparse(v)
    if not parsed.scheme or not parsed.netloc:
        return f"{value!r} is not a valid URI (missing scheme or host)"
    return None


_check_url = _check_uri


def _check_domain(value: Any, _: dict) -> str | None:
    v = _need_str(value)
    if isinstance(v, tuple):
        return v[1]
    if not re.fullmatch(
        r"(?=.{1,253}\Z)([A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)+",
        v,
    ):
        return f"{value!r} is not a valid domain name"
    return None


def _check_slug(value: Any, _: dict) -> str | None:
    v = _need_str(value)
    if isinstance(v, tuple):
        return v[1]
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", v):
        return f"{value!r} is not a valid slug"
    return None


def _check_ipv4(value: Any, _: dict) -> str | None:
    try:
        ipaddress.IPv4Address(str(value))
    except (ipaddress.AddressValueError, ValueError):
        return f"{value!r} is not a valid IPv4 address"
    return None


def _check_ipv6(value: Any, _: dict) -> str | None:
    try:
        ipaddress.IPv6Address(str(value))
    except (ipaddress.AddressValueError, ValueError):
        return f"{value!r} is not a valid IPv6 address"
    return None


# ---------------------------------------------------------------------------
# Temporal
# ---------------------------------------------------------------------------


def _check_date(value: Any, _: dict) -> str | None:
    raw = str(value)
    try:
        date.fromisoformat(raw)
    except ValueError:
        return f"{value!r} is not a valid ISO date (YYYY-MM-DD)"
    return None


def _check_datetime(value: Any, _: dict) -> str | None:
    raw = str(value)
    try:
        datetime.fromisoformat(raw)
    except ValueError:
        return f"{value!r} is not a valid ISO datetime"
    return None


def _check_time(value: Any, _: dict) -> str | None:
    raw = str(value)
    try:
        time.fromisoformat(raw)
    except ValueError:
        return f"{value!r} is not a valid ISO time"
    return None


_check_iso_8601 = _check_datetime


# ---------------------------------------------------------------------------
# Identifiers / encoding
# ---------------------------------------------------------------------------


def _check_uuid(value: Any, _: dict) -> str | None:
    try:
        UUID(str(value))
    except (ValueError, AttributeError):
        return f"{value!r} is not a valid UUID"
    return None


def _check_json(value: Any, _: dict) -> str | None:
    if isinstance(value, (dict, list)):
        return None
    try:
        json.loads(str(value))
    except (TypeError, ValueError):
        return f"{value!r} is not valid JSON"
    return None


def _check_hex_color(value: Any, _: dict) -> str | None:
    v = _need_str(value)
    if isinstance(v, tuple):
        return v[1]
    if not re.fullmatch(r"#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", v):
        return f"{value!r} is not a valid hex colour"
    return None


# ---------------------------------------------------------------------------
# Finance
# ---------------------------------------------------------------------------


_IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$")


def _check_iban(value: Any, _: dict) -> str | None:
    v = _need_str(value)
    if isinstance(v, tuple):
        return v[1]
    candidate = re.sub(r"\s+", "", v).upper()
    if not _IBAN_RE.fullmatch(candidate):
        return f"{value!r} is not a syntactically valid IBAN"
    # ISO 7064 mod-97 check
    rearranged = candidate[4:] + candidate[:4]
    numeric = "".join(str(int(c, 36)) if c.isalpha() else c for c in rearranged)
    if int(numeric) % 97 != 1:
        return f"{value!r} fails the IBAN checksum"
    return None


def _check_bic(value: Any, _: dict) -> str | None:
    v = _need_str(value)
    if isinstance(v, tuple):
        return v[1]
    if not re.fullmatch(r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?", v.upper()):
        return f"{value!r} is not a valid BIC/SWIFT code"
    return None


def _check_credit_card(value: Any, _: dict) -> str | None:
    raw = re.sub(r"\D", "", str(value))
    if not (13 <= len(raw) <= 19):
        return f"{value!r} is not a credit card length (13-19 digits)"
    # Luhn checksum
    digits = [int(d) for d in raw]
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    if checksum % 10 != 0:
        return f"{value!r} fails the Luhn checksum"
    return None


_ISO_4217 = {
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "CHF",
    "AUD",
    "CAD",
    "CNY",
    "SEK",
    "NZD",
    "MXN",
    "SGD",
    "HKD",
    "NOK",
    "KRW",
    "TRY",
    "RUB",
    "INR",
    "BRL",
    "ZAR",
    "AED",
    "ARS",
    "BGN",
    "CLP",
    "COP",
    "CZK",
    "DKK",
    "EGP",
    "HRK",
    "HUF",
    "IDR",
    "ILS",
    "ISK",
    "MAD",
    "MYR",
    "NGN",
    "PEN",
    "PHP",
    "PLN",
    "QAR",
    "RON",
    "SAR",
    "THB",
    "TWD",
    "UAH",
    "VND",
}


def _check_currency_code(value: Any, _: dict) -> str | None:
    v = _need_str(value)
    if isinstance(v, tuple):
        return v[1]
    if v.upper() not in _ISO_4217:
        return f"{value!r} is not a recognised ISO 4217 currency code"
    return None


def _check_amount(value: Any, params: dict) -> str | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return f"{value!r} is not a number"
    if not params.get("allow_zero", False) and num == 0:
        return f"{value!r} amount must be non-zero"
    if not params.get("allow_negative", False) and num < 0:
        return f"{value!r} amount must be positive"
    return None


# ---------------------------------------------------------------------------
# Telephony
# ---------------------------------------------------------------------------


def _check_phone_e164(value: Any, _: dict) -> str | None:
    raw = re.sub(r"\s|-|\(|\)", "", str(value))
    if not re.fullmatch(r"\+?[1-9]\d{6,14}", raw):
        return f"{value!r} is not a valid E.164 phone number"
    return None


# ---------------------------------------------------------------------------
# Geographic
# ---------------------------------------------------------------------------


def _check_country_code(value: Any, _: dict) -> str | None:
    v = _need_str(value)
    if isinstance(v, tuple):
        return v[1]
    if not re.fullmatch(r"[A-Z]{2}", v.upper()):
        return f"{value!r} is not a valid ISO 3166-1 alpha-2 country code"
    return None


def _check_language_code(value: Any, _: dict) -> str | None:
    v = _need_str(value)
    if isinstance(v, tuple):
        return v[1]
    if not re.fullmatch(r"[a-z]{2}", v.lower()):
        return f"{value!r} is not a valid ISO 639-1 language code"
    return None


def _check_postal_code(value: Any, params: dict) -> str | None:
    v = _need_str(value)
    if isinstance(v, tuple):
        return v[1]
    country = (params.get("country") or "").upper()
    patterns = {
        "ES": r"\d{5}",
        "FR": r"\d{5}",
        "DE": r"\d{5}",
        "IT": r"\d{5}",
        "GB": r"[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}",
        "US": r"\d{5}(-\d{4})?",
        "PT": r"\d{4}-\d{3}",
        "NL": r"\d{4}\s?[A-Z]{2}",
        "BE": r"\d{4}",
        "BR": r"\d{5}-?\d{3}",
    }
    pattern = patterns.get(country, r"[A-Za-z0-9\- ]{2,12}")
    if not re.fullmatch(pattern, v, flags=re.IGNORECASE):
        return f"{value!r} is not a valid postal code for {country or 'unknown country'}"
    return None


def _check_latitude(value: Any, _: dict) -> str | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return f"{value!r} is not numeric"
    if not -90.0 <= num <= 90.0:
        return f"latitude {num} is outside [-90, 90]"
    return None


def _check_longitude(value: Any, _: dict) -> str | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return f"{value!r} is not numeric"
    if not -180.0 <= num <= 180.0:
        return f"longitude {num} is outside [-180, 180]"
    return None


# ---------------------------------------------------------------------------
# National identifiers
# ---------------------------------------------------------------------------


_NIF_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"


def _check_nif(value: Any, _: dict) -> str | None:
    v = _need_str(value)
    if isinstance(v, tuple):
        return v[1]
    raw = v.upper().replace("-", "").replace(" ", "")
    if not re.fullmatch(r"\d{8}[A-Z]", raw):
        return f"{value!r} is not a Spanish NIF (8 digits + 1 letter)"
    if raw[-1] != _NIF_LETTERS[int(raw[:8]) % 23]:
        return f"{value!r} fails the Spanish NIF checksum"
    return None


def _check_nie(value: Any, _: dict) -> str | None:
    v = _need_str(value)
    if isinstance(v, tuple):
        return v[1]
    raw = v.upper().replace("-", "").replace(" ", "")
    if not re.fullmatch(r"[XYZ]\d{7}[A-Z]", raw):
        return f"{value!r} is not a Spanish NIE"
    initial = {"X": "0", "Y": "1", "Z": "2"}[raw[0]]
    numeric = int(initial + raw[1:8])
    if raw[-1] != _NIF_LETTERS[numeric % 23]:
        return f"{value!r} fails the Spanish NIE checksum"
    return None


def _check_cif(value: Any, _: dict) -> str | None:
    v = _need_str(value)
    if isinstance(v, tuple):
        return v[1]
    raw = v.upper().replace("-", "").replace(" ", "")
    if not re.fullmatch(r"[ABCDEFGHJKLMNPQRSUVW]\d{7}[A-J0-9]", raw):
        return f"{value!r} is not a Spanish CIF"
    digits = raw[1:8]
    odd_sum = sum(_double_digit(int(d)) for i, d in enumerate(digits) if i % 2 == 0)
    even_sum = sum(int(d) for i, d in enumerate(digits) if i % 2 == 1)
    total = odd_sum + even_sum
    control_digit = (10 - (total % 10)) % 10
    control_letter = "JABCDEFGHI"[control_digit]
    expected_alpha = raw[0] in "PQRSNW"
    last = raw[-1]
    if expected_alpha:
        if last != control_letter:
            return f"{value!r} fails the Spanish CIF checksum"
    else:
        if last != str(control_digit) and last != control_letter:
            return f"{value!r} fails the Spanish CIF checksum"
    return None


def _double_digit(d: int) -> int:
    doubled = d * 2
    return doubled if doubled < 10 else doubled - 9


def _check_vat_id(value: Any, params: dict) -> str | None:
    v = _need_str(value)
    if isinstance(v, tuple):
        return v[1]
    country = (params.get("country") or v[:2]).upper()
    raw = v.upper().replace(" ", "").replace("-", "")
    # Strip any prefix matching the country code.
    if raw.startswith(country):
        raw = raw[2:]
    patterns = {
        "ES": r"[A-Z\d]\d{7}[A-Z\d]",
        "FR": r"[A-Z\d]{2}\d{9}",
        "DE": r"\d{9}",
        "IT": r"\d{11}",
        "PT": r"\d{9}",
        "NL": r"\d{9}B\d{2}",
        "BE": r"\d{9,10}",
        "AT": r"U\d{8}",
    }
    pattern = patterns.get(country, r"[A-Z\d]{4,14}")
    if not re.fullmatch(pattern, raw):
        return f"{value!r} is not a valid {country} VAT number"
    return None


def _check_ssn(value: Any, _: dict) -> str | None:
    v = _need_str(value)
    if isinstance(v, tuple):
        return v[1]
    if not re.fullmatch(r"\d{3}-?\d{2}-?\d{4}", v):
        return f"{value!r} is not a valid US SSN"
    return None


def _check_passport_number(value: Any, _: dict) -> str | None:
    v = _need_str(value)
    if isinstance(v, tuple):
        return v[1]
    if not re.fullmatch(r"[A-Z0-9]{5,12}", v.upper()):
        return f"{value!r} is not a passport-shaped identifier"
    return None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


CHECKERS = {
    ValidatorType.EMAIL: _check_email,
    ValidatorType.URI: _check_uri,
    ValidatorType.URL: _check_url,
    ValidatorType.IPV4: _check_ipv4,
    ValidatorType.IPV6: _check_ipv6,
    ValidatorType.DOMAIN: _check_domain,
    ValidatorType.SLUG: _check_slug,
    ValidatorType.DATE: _check_date,
    ValidatorType.DATETIME: _check_datetime,
    ValidatorType.TIME: _check_time,
    ValidatorType.ISO_8601: _check_iso_8601,
    ValidatorType.UUID: _check_uuid,
    ValidatorType.JSON: _check_json,
    ValidatorType.HEX_COLOR: _check_hex_color,
    ValidatorType.IBAN: _check_iban,
    ValidatorType.BIC: _check_bic,
    ValidatorType.CREDIT_CARD: _check_credit_card,
    ValidatorType.CURRENCY_CODE: _check_currency_code,
    ValidatorType.AMOUNT: _check_amount,
    ValidatorType.PHONE_E164: _check_phone_e164,
    ValidatorType.COUNTRY_CODE: _check_country_code,
    ValidatorType.LANGUAGE_CODE: _check_language_code,
    ValidatorType.POSTAL_CODE: _check_postal_code,
    ValidatorType.LATITUDE: _check_latitude,
    ValidatorType.LONGITUDE: _check_longitude,
    ValidatorType.NIF: _check_nif,
    ValidatorType.NIE: _check_nie,
    ValidatorType.CIF: _check_cif,
    ValidatorType.VAT_ID: _check_vat_id,
    ValidatorType.SSN: _check_ssn,
    ValidatorType.PASSPORT_NUMBER: _check_passport_number,
}


def run_validator(validator_type: ValidatorType, value: Any, params: dict) -> str | None:
    """Look the checker up and run it. Returns ``None`` on success."""
    checker = CHECKERS.get(validator_type)
    if checker is None:
        return f"Unknown validator: {validator_type.value!r}"
    return checker(value, params)


class ValidatorRegistry:
    """Thin object wrapper exposing the validator catalogue as a service.

    Existing callers can use ``run_validator()`` directly; this class is
    provided for callers that prefer DI-style injection.
    """

    @staticmethod
    def run(validator_type: ValidatorType, value: Any, params: dict) -> str | None:
        return run_validator(validator_type, value, params)


__all__ = ["CHECKERS", "ValidatorRegistry", "run_validator"]
