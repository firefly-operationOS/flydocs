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
