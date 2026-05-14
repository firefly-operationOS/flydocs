# Copyright 2026 Firefly Software Solutions Inc
"""``StandardValidatorSpec`` -- request-side declaration for one built-in check."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from flydesk_idp.interfaces.enums.standard_validator import StandardValidatorType


class StandardValidatorSpec(BaseModel):
    """One named built-in validator applied to a field.

    Examples::

        {"type": "iban"}                       # generic
        {"type": "phone_e164", "params": {"country": "ES"}}
        {"type": "vat_id",     "params": {"country": "ES"}, "severity": "warning"}

    ``severity`` distinguishes hard errors (``error`` -- field is
    ``valid=false``) from soft warnings (``warning`` -- error is
    recorded but the field stays ``valid=true``). ``error`` is the
    default.
    """

    type: StandardValidatorType
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Literal["error", "warning"] = "error"
