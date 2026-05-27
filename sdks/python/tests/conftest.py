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

"""Shared pytest fixtures for the Python SDK test suite.

Centralises the base URL the tests pretend the SDK is talking to so
``respx`` mounts intercept on the same host every test uses, and
provides freshly-constructed clients per test so connection pools and
event loops don't leak across tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio

from flydocs_sdk import AsyncClient, Client

BASE_URL = "https://flydocs.test"


@pytest_asyncio.fixture
async def async_client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(BASE_URL) as client:
        yield client


@pytest.fixture
def sync_client() -> Iterator[Client]:
    with Client(BASE_URL) as client:
        yield client
