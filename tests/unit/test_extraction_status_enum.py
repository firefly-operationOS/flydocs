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

"""``ExtractionStatus`` / ``PostProcessingStatus`` semantic predicates."""

from __future__ import annotations

import pytest

from flydocs.interfaces.enums.extraction_status import ExtractionStatus, PostProcessingStatus


@pytest.mark.parametrize(
    "status",
    [ExtractionStatus.SUCCEEDED, ExtractionStatus.FAILED, ExtractionStatus.CANCELLED],
)
def test_terminal_statuses(status: ExtractionStatus) -> None:
    assert status.is_terminal


@pytest.mark.parametrize(
    "status",
    [
        ExtractionStatus.QUEUED,
        ExtractionStatus.RUNNING,
    ],
)
def test_non_terminal_statuses(status: ExtractionStatus) -> None:
    assert not status.is_terminal


def test_only_succeeded_has_result() -> None:
    """In v1 only ``succeeded`` carries a readable result.

    Partial / refining states are gone; bbox refinement is purely
    additive post-processing on a fully-succeeded result.
    """
    assert ExtractionStatus.SUCCEEDED.has_result is True


@pytest.mark.parametrize(
    "status",
    [
        ExtractionStatus.QUEUED,
        ExtractionStatus.RUNNING,
        ExtractionStatus.FAILED,
        ExtractionStatus.CANCELLED,
    ],
)
def test_statuses_without_readable_result(status: ExtractionStatus) -> None:
    assert not status.has_result


def test_extraction_status_string_values() -> None:
    """Lowercase wire values: the migration + repository depend on these strings."""
    assert ExtractionStatus.QUEUED.value == "queued"
    assert ExtractionStatus.RUNNING.value == "running"
    assert ExtractionStatus.SUCCEEDED.value == "succeeded"
    assert ExtractionStatus.FAILED.value == "failed"
    assert ExtractionStatus.CANCELLED.value == "cancelled"


def test_post_processing_status_values() -> None:
    # Stable wire values -- the migration + repository depend on these strings.
    assert PostProcessingStatus.PENDING.value == "pending"
    assert PostProcessingStatus.RUNNING.value == "running"
    assert PostProcessingStatus.SUCCEEDED.value == "succeeded"
    assert PostProcessingStatus.FAILED.value == "failed"


def test_post_processing_status_terminal_predicate() -> None:
    assert PostProcessingStatus.SUCCEEDED.is_terminal
    assert PostProcessingStatus.FAILED.is_terminal
    assert not PostProcessingStatus.PENDING.is_terminal
    assert not PostProcessingStatus.RUNNING.is_terminal
