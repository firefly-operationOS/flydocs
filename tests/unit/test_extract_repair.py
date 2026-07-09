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

"""Unit tests for :meth:`MultimodalExtractor.extract_repair`."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from flydocs.core.services.extraction import extractor as extractor_module
from flydocs.core.services.extraction.extractor import MultimodalExtractor
from flydocs.core.services.extraction.prompts import PromptCatalog
from flydocs.interfaces.dtos.document_type import DocumentTypeSpec
from flydocs.interfaces.dtos.field import Field, FieldGroup
from flydocs.interfaces.enums.field_type import FieldType


def _doc_subset() -> DocumentTypeSpec:
    return DocumentTypeSpec(
        id="passport",
        description="x",
        country="ES",
        field_groups=[
            FieldGroup(
                name="identity",
                fields=[Field(name="number", description="x", type=FieldType.STRING)],
            )
        ],
    )


@pytest.mark.asyncio
async def test_extract_repair_renders_evidence_and_uses_repair_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = PromptCatalog.from_resources()
    captured: dict[str, Any] = {}

    class _EmptyOutput(BaseModel):
        pass

    async def _fake_run(agent: Any, content: list[Any], *, op: str, model: str) -> Any:
        captured["op"] = op
        captured["model"] = model
        captured["user_text"] = content[0]
        # Minimal structured output: normalise_doc fills the spec shape
        # with null values when the payload is empty.
        return SimpleNamespace(output=_EmptyOutput())

    monkeypatch.setattr(extractor_module, "timed_agent_run", _fake_run)

    ext = MultimodalExtractor(
        template=catalog.extract,
        repair_template=catalog.extract_repair,
        model="base-model",
    )
    groups = await ext.extract_repair(
        document_bytes=b"%PDF-1.4",
        media_type="application/pdf",
        page_count=2,
        doc=_doc_subset(),
        failing_fields_text="- ``identity.number``: previous value 'X1' -- rejected because: misread",
        model="test",
    )

    # normalise_doc shapes the output after the subset spec.
    assert [g.name for g in groups] == ["identity"]
    assert [f.name for f in groups[0].fields] == ["number"]
    assert captured["op"] == "extract.repair"
    assert captured["model"] == "test"
    assert "misread" in captured["user_text"]
    assert "identity.number" in captured["user_text"]


@pytest.mark.asyncio
async def test_extract_repair_without_template_returns_empty() -> None:
    catalog = PromptCatalog.from_resources()
    ext = MultimodalExtractor(template=catalog.extract, model="base-model")
    groups = await ext.extract_repair(
        document_bytes=b"%PDF-1.4",
        media_type="application/pdf",
        page_count=1,
        doc=_doc_subset(),
        failing_fields_text="x",
    )
    assert groups == []
