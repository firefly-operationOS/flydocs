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

"""Tests for the job-polling convenience helper.

We pin three behaviours:

1. The helper polls ``GET /api/v1/jobs/{id}`` until it sees a terminal
   status, then returns.
2. It raises :class:`TimeoutError` if the deadline elapses before the
   worker finishes.
3. The terminal-status set covers ``SUCCEEDED`` / ``PARTIAL_SUCCEEDED``
   / ``FAILED`` / ``CANCELLED``.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from flydocs_sdk import AsyncFlydocsClient, JobStatus

BASE_URL = "http://flydocs.test"


def _status_body(status: str) -> dict[str, str]:
    return {
        "job_id": "job-1",
        "status": status,
        "submitted_at": "2026-05-17T10:00:00+00:00",
    }


@respx.mock
async def test_wait_for_completion_succeeds(async_client: AsyncFlydocsClient) -> None:
    # Three polls: QUEUED -> RUNNING -> SUCCEEDED. The helper should
    # return on the third poll without raising.
    respx.get(f"{BASE_URL}/api/v1/jobs/job-1").mock(
        side_effect=[
            httpx.Response(200, json=_status_body("QUEUED")),
            httpx.Response(200, json=_status_body("RUNNING")),
            httpx.Response(200, json=_status_body("SUCCEEDED")),
        ]
    )
    final = await async_client.wait_for_completion("job-1", poll_interval=0.001, timeout=5.0)
    assert final.status is JobStatus.SUCCEEDED


@respx.mock
async def test_wait_for_completion_returns_on_failure(
    async_client: AsyncFlydocsClient,
) -> None:
    respx.get(f"{BASE_URL}/api/v1/jobs/job-1").mock(
        return_value=httpx.Response(200, json=_status_body("FAILED"))
    )
    final = await async_client.wait_for_completion("job-1", poll_interval=0.001, timeout=5.0)
    # FAILED is terminal too — caller decides what to do with it; the
    # helper does NOT raise.
    assert final.status is JobStatus.FAILED


@respx.mock
async def test_wait_for_completion_returns_on_cancelled(
    async_client: AsyncFlydocsClient,
) -> None:
    respx.get(f"{BASE_URL}/api/v1/jobs/job-1").mock(
        return_value=httpx.Response(200, json=_status_body("CANCELLED"))
    )
    final = await async_client.wait_for_completion("job-1", poll_interval=0.001, timeout=5.0)
    assert final.status is JobStatus.CANCELLED


@respx.mock
async def test_wait_for_completion_returns_on_partial_succeeded(
    async_client: AsyncFlydocsClient,
) -> None:
    respx.get(f"{BASE_URL}/api/v1/jobs/job-1").mock(
        return_value=httpx.Response(200, json=_status_body("PARTIAL_SUCCEEDED"))
    )
    final = await async_client.wait_for_completion("job-1", poll_interval=0.001, timeout=5.0)
    assert final.status is JobStatus.PARTIAL_SUCCEEDED


@respx.mock
async def test_wait_for_completion_times_out(async_client: AsyncFlydocsClient) -> None:
    respx.get(f"{BASE_URL}/api/v1/jobs/job-1").mock(
        return_value=httpx.Response(200, json=_status_body("RUNNING"))
    )
    with pytest.raises(TimeoutError, match="did not reach a terminal status"):
        await async_client.wait_for_completion("job-1", poll_interval=0.001, timeout=0.05)


@respx.mock
async def test_wait_for_completion_refining_bboxes_is_not_terminal(
    async_client: AsyncFlydocsClient,
) -> None:
    # REFINING_BBOXES is an intermediate state — the helper should keep
    # polling until the bbox refiner finishes.
    respx.get(f"{BASE_URL}/api/v1/jobs/job-1").mock(
        side_effect=[
            httpx.Response(200, json=_status_body("REFINING_BBOXES")),
            httpx.Response(200, json=_status_body("SUCCEEDED")),
        ]
    )
    final = await async_client.wait_for_completion("job-1", poll_interval=0.001, timeout=5.0)
    assert final.status is JobStatus.SUCCEEDED
