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

"""Async extraction CQRS handlers."""

from flydocs.core.services.extractions.cancel_extraction_handler import (
    CancelExtractionCommand,
    CancelExtractionHandler,
)
from flydocs.core.services.extractions.get_extraction_handler import (
    GetExtractionHandler,
    GetExtractionQuery,
)
from flydocs.core.services.extractions.get_extraction_result_handler import (
    GetExtractionResultHandler,
    GetExtractionResultQuery,
)
from flydocs.core.services.extractions.list_extractions_handler import (
    ListExtractionsHandler,
    ListExtractionsQuery,
)
from flydocs.core.services.extractions.submit_extraction_handler import (
    SubmitExtractionCommand,
    SubmitExtractionHandler,
)

__all__ = [
    "CancelExtractionCommand",
    "CancelExtractionHandler",
    "GetExtractionHandler",
    "GetExtractionQuery",
    "GetExtractionResultHandler",
    "GetExtractionResultQuery",
    "ListExtractionsHandler",
    "ListExtractionsQuery",
    "SubmitExtractionCommand",
    "SubmitExtractionHandler",
]
