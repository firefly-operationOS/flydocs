# Copyright 2026 Firefly Software Solutions Inc
"""``GetExtractionResultHandler`` -- final result reads + post-processing long-poll.

In v1 only ``succeeded`` carries a readable result. Bbox refinement is
purely additive post-processing on an already-succeeded result: the
result is already returnable, the optional ``wait_for_post_processing``
flag lets callers block until the bbox leg lands in a terminal state.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from flydocs.core.services.extractions.get_extraction_result_handler import (
    ExtractionNotReady,
    GetExtractionResultHandler,
    GetExtractionResultQuery,
)
from flydocs.interfaces.dtos.extract import ExtractionResult, PipelineMeta
from flydocs.interfaces.enums.extraction_status import (
    ExtractionStatus,
    PostProcessingStatus,
)


def _result_payload() -> dict[str, Any]:
    return ExtractionResult(
        id="ext_RESULT0000000000000000000000",
        files=[],
        documents=[],
        pipeline=PipelineMeta(model="m", latency_ms=10),
    ).model_dump(mode="json", by_alias=True)


@dataclass
class _StubExtraction:
    id: str = "ext_TEST0000000000000000000000A"
    status: str = ExtractionStatus.SUCCEEDED.value
    result_json: dict[str, Any] | None = field(default_factory=_result_payload)
    submitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    post_processing_bbox_status: str | None = None


class _StubRepo:
    """Repository stub that can flip status mid-poll to simulate the refiner."""

    def __init__(
        self,
        ext: _StubExtraction,
        *,
        flip_bbox_to: str | None = None,
        after_calls: int = 0,
    ) -> None:
        self.ext = ext
        self.flip_bbox_to = flip_bbox_to
        self.after_calls = after_calls
        self.calls = 0

    async def get(self, ext_id: str) -> _StubExtraction | None:
        self.calls += 1
        if self.flip_bbox_to is not None and self.calls > self.after_calls:
            self.ext.post_processing_bbox_status = self.flip_bbox_to
        return self.ext if self.ext.id == ext_id else None


@pytest.mark.asyncio
async def test_returns_result_for_succeeded() -> None:
    handler = GetExtractionResultHandler(repository=_StubRepo(_StubExtraction()))  # type: ignore[arg-type]
    out = await handler.do_handle(GetExtractionResultQuery(extraction_id="ext_TEST0000000000000000000000A"))
    assert out is not None
    assert out.id == "ext_TEST0000000000000000000000A"


@pytest.mark.asyncio
async def test_returns_result_when_bbox_leg_pending() -> None:
    """The main pipeline is succeeded; the bbox leg is additive post-processing."""
    ext = _StubExtraction(post_processing_bbox_status=PostProcessingStatus.PENDING.value)
    handler = GetExtractionResultHandler(repository=_StubRepo(ext))  # type: ignore[arg-type]
    out = await handler.do_handle(GetExtractionResultQuery(extraction_id="ext_TEST0000000000000000000000A"))
    assert out is not None
    assert out.id == "ext_TEST0000000000000000000000A"


@pytest.mark.asyncio
async def test_returns_result_when_bbox_leg_running() -> None:
    ext = _StubExtraction(post_processing_bbox_status=PostProcessingStatus.RUNNING.value)
    handler = GetExtractionResultHandler(repository=_StubRepo(ext))  # type: ignore[arg-type]
    out = await handler.do_handle(GetExtractionResultQuery(extraction_id="ext_TEST0000000000000000000000A"))
    assert out is not None


@pytest.mark.asyncio
async def test_raises_not_ready_for_queued() -> None:
    ext = _StubExtraction(status=ExtractionStatus.QUEUED.value, result_json=None)
    handler = GetExtractionResultHandler(repository=_StubRepo(ext))  # type: ignore[arg-type]
    with pytest.raises(ExtractionNotReady) as ei:
        await handler.do_handle(GetExtractionResultQuery(extraction_id="ext_TEST0000000000000000000000A"))
    assert ei.value.status == ExtractionStatus.QUEUED


@pytest.mark.asyncio
async def test_raises_not_ready_for_failed() -> None:
    ext = _StubExtraction(status=ExtractionStatus.FAILED.value, result_json=None)
    handler = GetExtractionResultHandler(repository=_StubRepo(ext))  # type: ignore[arg-type]
    with pytest.raises(ExtractionNotReady) as ei:
        await handler.do_handle(GetExtractionResultQuery(extraction_id="ext_TEST0000000000000000000000A"))
    assert ei.value.status == ExtractionStatus.FAILED


@pytest.mark.asyncio
async def test_returns_none_for_unknown_extraction() -> None:
    handler = GetExtractionResultHandler(
        repository=_StubRepo(_StubExtraction(id="other"))  # type: ignore[arg-type]
    )
    out = await handler.do_handle(GetExtractionResultQuery(extraction_id="missing"))
    assert out is None


@pytest.mark.asyncio
async def test_wait_for_post_processing_returns_at_timeout() -> None:
    """Bbox leg stays in pending; poll should return at timeout."""
    ext = _StubExtraction(post_processing_bbox_status=PostProcessingStatus.PENDING.value)
    handler = GetExtractionResultHandler(repository=_StubRepo(ext))  # type: ignore[arg-type]
    started = asyncio.get_running_loop().time()
    out = await handler.do_handle(
        GetExtractionResultQuery(
            extraction_id="ext_TEST0000000000000000000000A",
            wait_for_post_processing=True,
            timeout_s=0.3,
            poll_interval_s=0.1,
        )
    )
    elapsed = asyncio.get_running_loop().time() - started
    assert out is not None
    assert 0.2 < elapsed < 1.0  # respected the timeout


@pytest.mark.asyncio
async def test_wait_for_post_processing_returns_early_when_bbox_succeeds() -> None:
    """Bbox leg flips from pending to succeeded mid-poll -> exit early."""
    ext = _StubExtraction(post_processing_bbox_status=PostProcessingStatus.PENDING.value)
    handler = GetExtractionResultHandler(
        repository=_StubRepo(  # type: ignore[arg-type]
            ext, flip_bbox_to=PostProcessingStatus.SUCCEEDED.value, after_calls=2
        )
    )
    started = asyncio.get_running_loop().time()
    out = await handler.do_handle(
        GetExtractionResultQuery(
            extraction_id="ext_TEST0000000000000000000000A",
            wait_for_post_processing=True,
            timeout_s=10.0,
            poll_interval_s=0.1,
        )
    )
    elapsed = asyncio.get_running_loop().time() - started
    assert out is not None
    assert ext.post_processing_bbox_status == PostProcessingStatus.SUCCEEDED.value
    assert elapsed < 5.0  # well under the 10s timeout
