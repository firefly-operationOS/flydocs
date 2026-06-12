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

"""Unit tests for :class:`ExtractionWorker` retry hardening.

We hit the private classification helper and the backoff math directly
rather than spinning the worker against a real queue -- the orchestration
plumbing is exercised in the LLM smoke tests.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from flydocs.core.services.workers.job_worker import ExtractionWorker, _is_permanent

# -- classification --------------------------------------------------------


def test_value_error_is_permanent() -> None:
    assert _is_permanent(ValueError("bad payload")) is True


def test_type_error_is_permanent() -> None:
    assert _is_permanent(TypeError("nope")) is True


def test_content_policy_message_is_permanent() -> None:
    assert _is_permanent(RuntimeError("the request was blocked by content policy")) is True


def test_invalid_api_key_is_permanent() -> None:
    assert _is_permanent(RuntimeError("Invalid API key provided")) is True


def test_unsupported_model_is_permanent() -> None:
    assert _is_permanent(RuntimeError("model_not_found: 'gpt-99'")) is True


def test_timeout_is_retryable() -> None:
    assert _is_permanent(TimeoutError("anthropic timed out")) is False


def test_network_glitch_is_retryable() -> None:
    assert _is_permanent(RuntimeError("connection reset")) is False


def test_generic_runtime_error_is_retryable() -> None:
    assert _is_permanent(RuntimeError("something else broke")) is False


# -- backoff math ----------------------------------------------------------


def _worker_with(base: float, ceiling: float) -> ExtractionWorker:
    settings = MagicMock()
    settings.eda_adapter = "memory"
    settings.retry_base_delay_s = base
    settings.retry_max_delay_s = ceiling
    settings.job_max_attempts = 3
    return ExtractionWorker(
        orchestrator=MagicMock(),
        repository=MagicMock(),
        event_publisher=MagicMock(),
        webhook=MagicMock(),
        settings=settings,
        consumer_id="test-worker",
    )


def test_backoff_grows_exponentially() -> None:
    worker = _worker_with(base=5.0, ceiling=300.0)
    # attempt N => raw = base * 2^(N-1). Jitter is up to 20% extra.
    d1 = worker._backoff_delay(1)
    d2 = worker._backoff_delay(2)
    d3 = worker._backoff_delay(3)
    assert 5.0 <= d1 < 6.1
    assert 10.0 <= d2 < 12.1
    assert 20.0 <= d3 < 24.1


def test_backoff_capped_at_ceiling() -> None:
    worker = _worker_with(base=5.0, ceiling=60.0)
    # attempt 10 raw = 5 * 512 = 2560; capped at 60 + up to 20% jitter
    d = worker._backoff_delay(10)
    assert 60.0 <= d <= 60.0 * 1.21


def test_backoff_attempt_one_is_base() -> None:
    worker = _worker_with(base=2.0, ceiling=300.0)
    d = worker._backoff_delay(1)
    assert 2.0 <= d < 2.5


# -- queued-backlog poll (durability fallback) -----------------------------


def _poll_worker(repo: MagicMock, interval: float = 0.01) -> ExtractionWorker:
    settings = MagicMock()
    settings.job_poll_interval_s = interval
    settings.job_poll_grace_s = 0
    settings.job_poll_batch = 10
    return ExtractionWorker(
        orchestrator=MagicMock(),
        repository=repo,
        event_publisher=MagicMock(),
        webhook=MagicMock(),
        settings=settings,
        consumer_id="test-worker",
    )


async def test_poll_claims_unnotified_queued_jobs() -> None:
    # A row left in `queued` because its NOTIFY was missed must still be picked up.
    repo = MagicMock()
    repo.find_stale_queued = AsyncMock(return_value=["ext_missed"])
    worker = _poll_worker(repo)
    processed: list[str] = []

    async def fake_process(extraction_id: str) -> None:
        processed.append(extraction_id)
        worker.stop()  # exit the loop after the first claim so the test is bounded

    worker._process = fake_process  # type: ignore[assignment]
    await asyncio.wait_for(worker._poll_queued_backlog(), timeout=2.0)

    assert processed == ["ext_missed"]
    repo.find_stale_queued.assert_awaited_with(older_than_seconds=0, limit=10)


async def test_poll_disabled_when_interval_zero() -> None:
    # interval <= 0 disables the poll entirely (NOTIFY + reaper only).
    repo = MagicMock()
    repo.find_stale_queued = AsyncMock()
    worker = _poll_worker(repo, interval=0)
    await asyncio.wait_for(worker._poll_queued_backlog(), timeout=1.0)
    repo.find_stale_queued.assert_not_awaited()
