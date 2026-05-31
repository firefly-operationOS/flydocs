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

""":class:`ListExtractionsHandler` -- pagination + filter contract.

The handler delegates to ``ExtractionRepository.list_extractions``; here
we mock the repository and assert (a) the right filter args travel
through, (b) the row mapping into :class:`Extraction` is faithful, and
(c) ``total`` reflects the filtered set independent of ``limit``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from flydocs.core.services.extractions.list_extractions_handler import (
    ListExtractionsHandler,
    ListExtractionsQuery,
)
from flydocs.interfaces.enums.extraction_status import (
    ExtractionStatus,
    PostProcessingStatus,
)


def _row(**overrides):
    base = {
        "id": "ext_TEST00000000000000000000001",
        "status": "succeeded",
        "submitted_at": datetime(2026, 5, 15, 10, 0, tzinfo=UTC),
        "started_at": datetime(2026, 5, 15, 10, 0, 1, tzinfo=UTC),
        "finished_at": datetime(2026, 5, 15, 10, 1, tzinfo=UTC),
        "attempts": 1,
        "error_code": None,
        "error_message": None,
        "post_processing_bbox_status": None,
        "post_processing_bbox_attempts": 0,
        "post_processing_bbox_started_at": None,
        "post_processing_bbox_finished_at": None,
        "post_processing_bbox_error_code": None,
        "post_processing_bbox_error_message": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_passes_filters_through_and_maps_rows() -> None:
    repository = MagicMock()
    repository.list_extractions = AsyncMock(
        return_value=(
            [
                _row(id="ext_AAA00000000000000000000001", status="succeeded"),
                _row(
                    id="ext_BBB00000000000000000000002",
                    status="succeeded",
                    post_processing_bbox_status="pending",
                ),
            ],
            42,  # total across the filter
        )
    )
    handler = ListExtractionsHandler(repository=repository)

    response = await handler.do_handle(
        ListExtractionsQuery(
            statuses=(ExtractionStatus.SUCCEEDED,),
            post_processing_statuses=(PostProcessingStatus.PENDING,),
            created_after=datetime(2026, 5, 15, tzinfo=UTC),
            limit=2,
            offset=0,
        )
    )

    repository.list_extractions.assert_awaited_once()
    kwargs = repository.list_extractions.await_args.kwargs
    assert kwargs["statuses"] == ["succeeded"]
    assert kwargs["post_processing_bbox_statuses"] == ["pending"]
    assert kwargs["limit"] == 2
    assert kwargs["offset"] == 0

    assert response.total == 42  # filtered total, not limited
    assert response.limit == 2
    assert response.offset == 0
    assert [i.id for i in response.items] == [
        "ext_AAA00000000000000000000001",
        "ext_BBB00000000000000000000002",
    ]
    assert response.items[0].status is ExtractionStatus.SUCCEEDED
    assert response.items[1].status is ExtractionStatus.SUCCEEDED
    # The second row carries a post_processing block with pending status.
    assert response.items[1].post_processing is not None
    assert response.items[1].post_processing.bbox_refinement.status is PostProcessingStatus.PENDING


@pytest.mark.asyncio
async def test_empty_filter_lists_passes_none_to_repository() -> None:
    """Empty tuples should become ``None`` so the repository builds no SQL clause."""
    repository = MagicMock()
    repository.list_extractions = AsyncMock(return_value=([], 0))
    handler = ListExtractionsHandler(repository=repository)

    await handler.do_handle(ListExtractionsQuery())

    kwargs = repository.list_extractions.await_args.kwargs
    assert kwargs["statuses"] is None
    assert kwargs["post_processing_bbox_statuses"] is None
    assert kwargs["idempotency_key"] is None


@pytest.mark.asyncio
async def test_pagination_defaults() -> None:
    repository = MagicMock()
    repository.list_extractions = AsyncMock(return_value=([], 0))
    handler = ListExtractionsHandler(repository=repository)

    response = await handler.do_handle(ListExtractionsQuery())

    assert response.limit == 50
    assert response.offset == 0
