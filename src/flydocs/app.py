# Copyright 2026 Firefly Software Solutions Inc
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
    version="0.1.0",
    description="Firefly Desk IDP -- multimodal document extraction with bounding boxes.",
    scan_packages=[
        "flydocs.core",  # @configuration class
        "flydocs.core.services.extract",  # extract command handler
        "flydocs.core.services.jobs",  # job command/query handlers
        "flydocs.web.controllers",  # REST controllers
        "flydocs.web.advice",  # exception advice
    ],
)
class FlydocsApplication:
    """Marker class consumed by :class:`PyFlyApplication` at boot."""
