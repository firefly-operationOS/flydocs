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

"""Unit tests for per-stage model routing.

Each LLM pipeline stage can be pinned to its own model via a
``FLYDOCS_<STAGE>_MODEL`` setting. Resolution precedence per stage:

    per-stage setting  >  ``options.model``  >  ``FLYDOCS_MODEL``
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from flydocs.config import IDPSettings
from flydocs.core.services.pipeline.orchestrator import (
    PipelineOrchestrator,
    _stage_models,
)
from flydocs.interfaces.dtos.document_type import DocumentTypeSpec
from flydocs.interfaces.dtos.extract import (
    ExtractionOptions,
    ExtractionRequest,
    FileInput,
    StageToggles,
)
from flydocs.interfaces.dtos.field import ExtractedFieldGroup, Field, FieldGroup
from flydocs.interfaces.enums.field_type import FieldType

_DUMMY = base64.b64encode(b"%PDF-1.4").decode("ascii")

_STAGES = (
    "splitter",
    "classifier",
    "extract",
    "visual_authenticity",
    "content_authenticity",
    "judge",
    "rules",
)


def test_settings_per_stage_models_default_none() -> None:
    settings = IDPSettings()
    assert settings.splitter_model is None
    assert settings.classifier_model is None
    assert settings.extract_model is None
    assert settings.visual_authenticity_model is None
    assert settings.content_authenticity_model is None
    assert settings.judge_model is None
    assert settings.rule_engine_model is None
    assert settings.transform_model is None
    assert settings.bbox_matcher_model is None


def test_settings_per_stage_models_env_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLYDOCS_JUDGE_MODEL", "anthropic:claude-opus-4-8")
    monkeypatch.setenv("FLYDOCS_CLASSIFIER_MODEL", "anthropic:claude-haiku-4-5")
    settings = IDPSettings()
    assert settings.judge_model == "anthropic:claude-opus-4-8"
    assert settings.classifier_model == "anthropic:claude-haiku-4-5"


def test_stage_models_all_fall_back_to_model_id() -> None:
    resolved = _stage_models(IDPSettings(), "default-model")
    assert set(resolved) == set(_STAGES)
    assert all(resolved[stage] == "default-model" for stage in _STAGES)


def test_stage_models_per_stage_setting_wins_over_request_model() -> None:
    settings = IDPSettings(
        judge_model="judge-pin",
        classifier_model="classifier-pin",
    )
    # "request-override" plays the role of ``options.model or FLYDOCS_MODEL``.
    resolved = _stage_models(settings, "request-override")
    assert resolved["judge"] == "judge-pin"
    assert resolved["classifier"] == "classifier-pin"
    for stage in set(_STAGES) - {"judge", "classifier"}:
        assert resolved[stage] == "request-override"


def _doc_spec() -> DocumentTypeSpec:
    return DocumentTypeSpec(
        id="passport",
        description="x",
        country="ES",
        field_groups=[
            FieldGroup(
                name="g",
                fields=[Field(name="a", description="x", type=FieldType.STRING)],
            )
        ],
    )


def _request() -> ExtractionRequest:
    return ExtractionRequest(
        intention="test",
        files=[FileInput(filename="doc.pdf", content_base64=_DUMMY, expected_type="passport")],
        document_types=[_doc_spec()],
        options=ExtractionOptions(stages=StageToggles(judge=True)),
    )


def _fake_normalizer() -> Any:
    row = SimpleNamespace(
        bytes=b"%PDF-1.4",
        media_type="application/pdf",
        page_count=1,
        filename="doc.pdf",
        derived_from=[],
    )
    normalizer = MagicMock()
    normalizer.normalise = AsyncMock(return_value=[row])
    return normalizer


def _orchestrator(settings: IDPSettings, extractor: Any, judge: Any) -> PipelineOrchestrator:
    field_validator = MagicMock()
    field_validator.validate_groups = MagicMock(return_value=None)
    bbox_validator = MagicMock()
    bbox_validator.validate_groups = MagicMock(return_value=None)
    return PipelineOrchestrator(
        extractor=extractor,
        splitter=MagicMock(),
        classifier=MagicMock(),
        field_validator=field_validator,
        bbox_validator=bbox_validator,
        bbox_refiner=MagicMock(),
        binary_normalizer=_fake_normalizer(),
        visual_checker=MagicMock(),
        content_checker=MagicMock(),
        judge=judge,
        rule_engine=MagicMock(),
        judge_escalator=MagicMock(),
        transformation_engine=MagicMock(),
        settings=settings,
        default_model="default-model",
    )


@pytest.mark.asyncio
async def test_orchestrator_routes_stage_pinned_models() -> None:
    """extract + judge receive their pinned models, not the shared default."""
    groups = [ExtractedFieldGroup(name="g", fields=[])]
    extractor = MagicMock()
    extractor.extract = AsyncMock(return_value=(groups, "extract-pin"))
    judge = MagicMock()
    judge.judge = AsyncMock(return_value=None)

    settings = IDPSettings(extract_model="extract-pin", judge_model="judge-pin")
    orchestrator = _orchestrator(settings, extractor, judge)

    result = await orchestrator.execute(_request())

    assert extractor.extract.await_count == 1
    assert extractor.extract.await_args.kwargs["model"] == "extract-pin"
    assert judge.judge.await_count == 1
    assert judge.judge.await_args.kwargs["model"] == "judge-pin"
    assert result.pipeline.model == "extract-pin"
