# Copyright 2026 Firefly Software Solutions Inc
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Official Python SDK for flydocs.

flydocs is a pure-multimodal Intelligent Document Processing service:
structured field extraction with bounding boxes, validation,
authenticity checks, LLM judge, and a business-rule engine.

This package gives Python callers a typed, async-first client over the
service's REST API, plus a synchronous wrapper for non-async code and a
helper for verifying outbound webhook signatures.

    from flydocs_sdk import FlydocsClient, DocumentInput, ExtractionRequest

    client = FlydocsClient("http://localhost:8400")
    result = client.extract(
        ExtractionRequest(
            documents=[DocumentInput.from_path("invoice.pdf")],
            docs=[{"docType": {"documentType": "invoice"}, "groups": [...]}],
        )
    )
    for doc in result.documents:
        for group in doc.fields:
            for field in group.field_group_fields:
                print(field.name, "=", field.value)
"""

from flydocs_sdk._version import __version__
from flydocs_sdk.async_client import AsyncFlydocsClient
from flydocs_sdk.client import FlydocsClient
from flydocs_sdk.errors import (
    FlydocsAPIError,
    FlydocsClientError,
    FlydocsError,
    FlydocsHTTPError,
    FlydocsTimeoutError,
)
from flydocs_sdk.models import (
    DocumentInput,
    ExtractionRequest,
    ExtractionResult,
    JobListResponse,
    JobResult,
    JobStatus,
    JobStatusResponse,
    JobWebhookPayload,
    SubmitJobRequest,
    SubmitJobResponse,
    VersionInfo,
)
from flydocs_sdk.webhooks import WebhookVerificationError, WebhookVerifier

__all__ = [
    "__version__",
    # Clients
    "AsyncFlydocsClient",
    "FlydocsClient",
    # Errors
    "FlydocsAPIError",
    "FlydocsClientError",
    "FlydocsError",
    "FlydocsHTTPError",
    "FlydocsTimeoutError",
    # Models
    "DocumentInput",
    "ExtractionRequest",
    "ExtractionResult",
    "JobListResponse",
    "JobResult",
    "JobStatus",
    "JobStatusResponse",
    "JobWebhookPayload",
    "SubmitJobRequest",
    "SubmitJobResponse",
    "VersionInfo",
    # Webhooks
    "WebhookVerificationError",
    "WebhookVerifier",
]
