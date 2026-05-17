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

"""Smoke tests for the synchronous client.

The sync client wraps the async one; full coverage of the endpoint
shapes lives in :mod:`tests.test_async_client`. The job of these tests
is to prove the sync wrapper plumbs everything through correctly --
the loop is created, calls succeed, and the context manager closes
cleanly.
"""

from __future__ import annotations

import httpx
import respx

from flydocs_sdk import FlydocsClient, JobStatus

BASE_URL = "http://flydocs.test"


@respx.mock
def test_sync_version() -> None:
    respx.get(f"{BASE_URL}/api/v1/version").mock(
        return_value=httpx.Response(
            200,
            json={
                "service": "flydocs",
                "version": "26.5.1",
                "model": "anthropic:claude-sonnet-4-6",
                "fallback_model": "",
                "eda_adapter": "postgres",
            },
        )
    )
    with FlydocsClient(BASE_URL) as client:
        info = client.version()
    assert info.service == "flydocs"


@respx.mock
def test_sync_submit_job_returns_typed_response() -> None:
    respx.post(f"{BASE_URL}/api/v1/jobs").mock(
        return_value=httpx.Response(
            202,
            json={
                "job_id": "job-sync",
                "status": "QUEUED",
                "submitted_at": "2026-05-17T10:00:00+00:00",
            },
        )
    )
    with FlydocsClient(BASE_URL) as client:
        resp = client.submit_job(
            {
                "documents": [{"filename": "x.pdf", "content_base64": "YWJj"}],
                "docs": [{"docType": {"documentType": "invoice"}}],
            }
        )
    assert resp.status is JobStatus.QUEUED


def test_sync_client_closed_after_exit() -> None:
    client = FlydocsClient(BASE_URL)
    client.close()
    # Second close is a no-op.
    client.close()
