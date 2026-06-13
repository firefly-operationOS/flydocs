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

"""Guard against version drift between the build metadata and the runtime.

The 26.6.6 release shipped with ``pyproject.toml`` at ``26.6.6`` but the
runtime ``__version__`` (and the ``app.py`` decorator literal) left at
``26.6.3``, so ``GET /api/v1/version`` and the OpenAPI document advertised a
stale version. ``app.py`` now derives its version from ``flydocs.__version__``
(one in-code source); this test pins that single source to ``pyproject.toml`` so
a half-done bump can never reach a release again.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import flydocs

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _pyproject_version() -> str:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def test_runtime_version_matches_pyproject() -> None:
    """``flydocs.__version__`` is the single in-code source and must equal the
    version declared in ``pyproject.toml``."""
    assert flydocs.__version__ == _pyproject_version()
