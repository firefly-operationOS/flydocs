# Copyright 2026 Firefly Software Solutions Inc
"""``ListExtractionsHandler`` -- paginated, filterable listing of extractions.

Exposed at ``GET /api/v1/extractions`` by :class:`ExtractionsController`.
Filters are optional and combine with ``AND``; the response is paginated
and the total reflects the FILTERED set (not the table size). Rows come
back ordered ``submitted_at DESC`` so the most recent activity surfaces
first in dashboards / operator tooling.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pyfly.container import service
from pyfly.cqrs import Query, QueryHandler, query_handler

from flydocs.core.services.extractions._projector import row_to_extraction
from flydocs.interfaces.dtos.extraction import ExtractionListResponse
from flydocs.interfaces.enums.extraction_status import (
    ExtractionStatus,
    PostProcessingStatus,
)
from flydocs.models.repositories import ExtractionRepository


@dataclass(frozen=True)
class ListExtractionsQuery(Query[ExtractionListResponse]):
    """Filters + pagination for ``GET /api/v1/extractions``."""

    statuses: tuple[ExtractionStatus, ...] = ()
    post_processing_statuses: tuple[PostProcessingStatus, ...] = ()
    created_after: datetime | None = None
    created_before: datetime | None = None
    idempotency_key: str | None = None
    limit: int = 50
    offset: int = 0


@query_handler
@service
class ListExtractionsHandler(QueryHandler[ListExtractionsQuery, ExtractionListResponse]):
    def __init__(self, repository: ExtractionRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: ListExtractionsQuery) -> ExtractionListResponse:
        rows, total = await self._repository.list_extractions(
            statuses=[s.value for s in query.statuses] or None,
            post_processing_bbox_statuses=[s.value for s in query.post_processing_statuses] or None,
            created_after=query.created_after,
            created_before=query.created_before,
            idempotency_key=query.idempotency_key,
            limit=query.limit,
            offset=query.offset,
        )
        items = [row_to_extraction(r) for r in rows]
        return ExtractionListResponse(items=items, total=total, limit=query.limit, offset=query.offset)


__all__ = ["ListExtractionsHandler", "ListExtractionsQuery"]
