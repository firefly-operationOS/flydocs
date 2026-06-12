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

"""PyFly application entry point for flydocs.

``scan_packages`` declares every package containing ``@configuration``,
``@rest_controller``, ``@controller_advice``, ``@service``,
``@command_handler``, ``@query_handler``, or ``@repository`` beans so
pyfly's DI container can discover them at boot.
"""

from __future__ import annotations

from pyfly.core import pyfly_application
from pyfly.starters.core import enable_core_stack


@enable_core_stack
@pyfly_application(
    name="flydocs",
    version="26.6.3",
    description=(
        "flydocs -- pure-multimodal document extraction with bounding "
        "boxes. Part of Firefly OperationOS, platform-agnostic."
    ),
    scan_packages=[
        "flydocs.core",  # @configuration class
        "flydocs.core.services.extract",  # sync extract command handler
        "flydocs.core.services.extractions",  # async extraction handlers
        "flydocs.web.controllers",  # REST controllers
        "flydocs.web.advice",  # exception advice
    ],
)
class FlydocsApplication:
    """Marker class consumed by :class:`PyFlyApplication` at boot."""
