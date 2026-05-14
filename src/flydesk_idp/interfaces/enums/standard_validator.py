# Copyright 2026 Firefly Software Solutions Inc
"""Built-in :class:`StandardValidator` types.

Standard validators are pure-Python value checks that the
:class:`FieldValidator` runs after extraction. They sit on top of the
simple ``pattern`` / ``format`` / ``enum`` / ``min`` / ``max`` constraints
and cover the high-value cases ("is this an IBAN?", "is this a valid
Spanish NIE?", "is this a credit card number?") that callers would
otherwise hand-roll.

Adding a new validator: append a member here, then implement
``_check_<name>`` in :mod:`flydesk_idp.core.services.validation.standard_validator_registry`.
"""

from __future__ import annotations

from enum import StrEnum


class StandardValidatorType(StrEnum):
    # --- network / web --------------------------------------------------
    EMAIL = "email"
    URI = "uri"
    IPV4 = "ipv4"
    IPV6 = "ipv6"
    DOMAIN = "domain"
    SLUG = "slug"
    URL = "url"

    # --- temporal --------------------------------------------------------
    DATE = "date"
    DATETIME = "datetime"
    TIME = "time"
    ISO_8601 = "iso_8601"

    # --- identifiers -----------------------------------------------------
    UUID = "uuid"
    JSON = "json"
    HEX_COLOR = "hex_color"

    # --- finance ---------------------------------------------------------
    IBAN = "iban"  # ISO 13616
    BIC = "bic"  # ISO 9362 (SWIFT)
    CREDIT_CARD = "credit_card"  # Luhn check
    CURRENCY_CODE = "currency_code"  # ISO 4217
    AMOUNT = "amount"  # numeric > 0

    # --- telephony -------------------------------------------------------
    PHONE_E164 = "phone_e164"  # ``+<country><number>``

    # --- geographic ------------------------------------------------------
    COUNTRY_CODE = "country_code"  # ISO 3166-1 alpha-2
    LANGUAGE_CODE = "language_code"  # ISO 639-1
    POSTAL_CODE = "postal_code"  # generic, country-aware
    LATITUDE = "latitude"
    LONGITUDE = "longitude"

    # --- national identifiers (require country param when ambiguous) ----
    NIF = "nif"  # ES -- person tax id
    NIE = "nie"  # ES -- foreign person tax id
    CIF = "cif"  # ES -- legacy company tax id
    VAT_ID = "vat_id"  # EU VAT number
    SSN = "ssn"  # US SSN
    PASSPORT_NUMBER = "passport_number"  # ICAO 9303 (length / charset only)
