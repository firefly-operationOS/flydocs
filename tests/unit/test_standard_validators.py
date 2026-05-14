# Copyright 2026 Firefly Software Solutions Inc
"""Unit tests for :class:`StandardValidator` checkers.

Covers the cases real callers rely on: email, IBAN checksum, NIF
checksum, Luhn credit card, phone E.164, lat/long bounds. Each test
has a tight reason; there is no shotgun coverage."""

from __future__ import annotations

import pytest

from flydesk_idp.core.services.validation.standard_validator_registry import run_standard_validator
from flydesk_idp.interfaces.enums.standard_validator import StandardValidatorType


@pytest.mark.parametrize(
    "value",
    ["jane.doe@example.com", "User+tag@example.co.uk"],
)
def test_email_accepts_valid(value: str) -> None:
    assert run_standard_validator(StandardValidatorType.EMAIL, value, {}) is None


@pytest.mark.parametrize("value", ["", "not-an-email", "@example.com", "x@y"])
def test_email_rejects_invalid(value: str) -> None:
    assert run_standard_validator(StandardValidatorType.EMAIL, value, {}) is not None


def test_iban_valid_checksum() -> None:
    # A canonical valid IBAN
    assert run_standard_validator(StandardValidatorType.IBAN, "GB82 WEST 1234 5698 7654 32", {}) is None


def test_iban_invalid_checksum() -> None:
    assert run_standard_validator(StandardValidatorType.IBAN, "GB82WEST12345698765499", {}) is not None


@pytest.mark.parametrize(
    "card,ok",
    [
        ("4242 4242 4242 4242", True),   # Stripe-test
        ("4111-1111-1111-1111", True),   # Visa-test
        ("4242 4242 4242 4243", False),  # Luhn fails
    ],
)
def test_credit_card_luhn(card: str, ok: bool) -> None:
    result = run_standard_validator(StandardValidatorType.CREDIT_CARD, card, {})
    if ok:
        assert result is None
    else:
        assert result is not None


def test_phone_e164() -> None:
    assert run_standard_validator(StandardValidatorType.PHONE_E164, "+34612345678", {}) is None
    assert run_standard_validator(StandardValidatorType.PHONE_E164, "abc", {}) is not None


def test_latitude_bounds() -> None:
    assert run_standard_validator(StandardValidatorType.LATITUDE, 12.34, {}) is None
    assert run_standard_validator(StandardValidatorType.LATITUDE, 91.0, {}) is not None


def test_nif_checksum_spanish() -> None:
    # Known-valid NIF (8 digits + control letter)
    assert run_standard_validator(StandardValidatorType.NIF, "12345678Z", {}) is None
    assert run_standard_validator(StandardValidatorType.NIF, "12345678A", {}) is not None


def test_nie_checksum_spanish() -> None:
    # Known-valid NIE
    assert run_standard_validator(StandardValidatorType.NIE, "X1234567L", {}) is None
    assert run_standard_validator(StandardValidatorType.NIE, "X1234567Z", {}) is not None


def test_postal_code_country_aware() -> None:
    es_ok = run_standard_validator(StandardValidatorType.POSTAL_CODE, "28013", {"country": "ES"})
    es_bad = run_standard_validator(StandardValidatorType.POSTAL_CODE, "ABC", {"country": "ES"})
    gb_ok = run_standard_validator(StandardValidatorType.POSTAL_CODE, "SW1A 1AA", {"country": "GB"})
    assert es_ok is None and es_bad is not None and gb_ok is None
