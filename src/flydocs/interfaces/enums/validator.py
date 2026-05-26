# Copyright 2026 Firefly Software Solutions Inc
"""Built-in validator catalogue applied to extracted field values.

Replaces the v0 ``StandardValidatorType`` — the "standard" prefix carried no
semantic distinction since there is only one validator catalogue in the
public API.
"""

from __future__ import annotations

from enum import StrEnum


class ValidatorType(StrEnum):
    # Network / web
    EMAIL = "email"
    URI = "uri"
    URL = "url"
    DOMAIN = "domain"
    SLUG = "slug"
    IPV4 = "ipv4"
    IPV6 = "ipv6"

    # Temporal
    DATE = "date"
    DATETIME = "datetime"
    TIME = "time"
    ISO_8601 = "iso_8601"

    # Identifiers
    UUID = "uuid"
    JSON = "json"
    HEX_COLOR = "hex_color"

    # Finance
    IBAN = "iban"
    BIC = "bic"
    CREDIT_CARD = "credit_card"
    CURRENCY_CODE = "currency_code"
    AMOUNT = "amount"

    # Telephony
    PHONE_E164 = "phone_e164"

    # Geographic
    COUNTRY_CODE = "country_code"
    LANGUAGE_CODE = "language_code"
    POSTAL_CODE = "postal_code"
    LATITUDE = "latitude"
    LONGITUDE = "longitude"

    # National IDs
    NIF = "nif"
    NIE = "nie"
    CIF = "cif"
    VAT_ID = "vat_id"
    SSN = "ssn"
    PASSPORT_NUMBER = "passport_number"
