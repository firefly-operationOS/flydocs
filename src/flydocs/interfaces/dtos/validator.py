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

"""ValidatorSpec -- request-side declaration for one built-in check.

Replaces the v0 ``StandardValidatorSpec``. ``name`` is the dispatch key
(was ``type`` in v0 — renamed to avoid collision with :class:`Field.type`
when both appear in the same parent envelope).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from flydocs.interfaces.enums.validator import ValidatorType


class ValidatorSpec(BaseModel):
    """One named built-in validator applied to a field.

    Examples::

        {"name": "iban"}
        {"name": "phone_e164", "params": {"country": "ES"}}
        {"name": "vat_id", "params": {"country": "ES"}, "severity": "warning"}

    ``severity`` distinguishes hard errors (``error`` -- field is
    ``valid=false``) from soft warnings (``warning`` -- error is recorded
    but the field stays ``valid=true``). ``error`` is the default.
    """

    model_config = ConfigDict(extra="forbid")

    name: ValidatorType
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Literal["error", "warning"] = "error"
