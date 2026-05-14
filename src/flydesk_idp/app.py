# Copyright 2026 Firefly Software Solutions Inc
"""PyFly application entry point for flydesk-idp.

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
    name="flydesk-idp",
    version="0.1.0",
    description="Firefly Desk IDP -- multimodal document extraction with bounding boxes.",
    scan_packages=[
        "flydesk_idp.core",  # @configuration class
        "flydesk_idp.core.services.extract",  # extract command handler
        "flydesk_idp.core.services.jobs",  # job command/query handlers
        "flydesk_idp.web.controllers",  # REST controllers
        "flydesk_idp.web.advice",  # exception advice
    ],
)
class FlydeskIDPApplication:
    """Marker class consumed by :class:`PyFlyApplication` at boot."""
