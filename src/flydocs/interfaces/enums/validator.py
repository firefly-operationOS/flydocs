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
