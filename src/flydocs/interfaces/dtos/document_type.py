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

"""DocumentTypeSpec -- schema template for one expected document type.

Replaces the v0 ``DocSpec`` and the nested ``DocType`` envelope, flattening
``docs[i].docType.documentType`` (three layers of "doc" stutter) into
``document_types[i].id`` (one identifier).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from flydocs.interfaces.dtos.field import FieldGroup


class VisualCheck(BaseModel):
    """One visual check to run against the document (signature, watermark, seal, ...)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    description: str


class DocumentTypeSpec(BaseModel):
    """One expected document type the caller is submitting fields for."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="Stable id (e.g. 'invoice', 'passport').")
    description: str | None = None
    country: str | None = Field(default=None, description="ISO 3166-1 alpha-2 country code.")
    field_groups: list[FieldGroup] = Field(..., min_length=1)
    visual_checks: list[VisualCheck] = Field(default_factory=list)
