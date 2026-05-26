# Copyright 2026 Firefly Software Solutions Inc
"""``GetExtractionHandler`` -- status lookup for an async extraction."""

from __future__ import annotations

from dataclasses import dataclass

from pyfly.container import service
from pyfly.cqrs import Query, QueryHandler, query_handler

from flydocs.core.services.extractions._projector import row_to_extraction
from flydocs.interfaces.dtos.extraction import Extraction
from flydocs.models.repositories import ExtractionRepository


@dataclass(frozen=True)
class GetExtractionQuery(Query[Extraction | None]):
    extraction_id: str


@query_handler
@service
class GetExtractionHandler(QueryHandler[GetExtractionQuery, Extraction | None]):
    def __init__(self, repository: ExtractionRepository) -> None:
        super().__init__()
        self._repository = repository

    async def do_handle(self, query: GetExtractionQuery) -> Extraction | None:
        row = await self._repository.get(query.extraction_id)
        if row is None:
            return None
        return row_to_extraction(row)


__all__ = ["GetExtractionHandler", "GetExtractionQuery"]
