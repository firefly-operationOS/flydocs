# Copyright 2026 Firefly Software Solutions Inc
"""ASGI entry point for flydesk-idp.

Loaded by ``pyfly run`` and by ``uvicorn flydesk_idp.main:app``. Builds
the :class:`PyFlyApplication`, scans every @rest_controller / @event_listener
under ``flydesk_idp``, and returns a FastAPI app with all routes
mounted, exception handlers wired, and middlewares attached.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from pyfly.core import PyFlyApplication
from pyfly.web.adapters.fastapi.app import create_app

from flydesk_idp.app import FlydeskIDPApplication

# Build PyFlyApplication: loads pyfly.yaml, scans packages, prepares the DI context.
_pyfly = PyFlyApplication(FlydeskIDPApplication)


@asynccontextmanager
async def _lifespan(app: Any):
    """Drive the framework's startup / shutdown lifecycle."""
    _pyfly._route_metadata = getattr(app.state, "pyfly_route_metadata", [])
    _pyfly._docs_enabled = getattr(app.state, "pyfly_docs_enabled", False)
    _pyfly._host = str(_pyfly.config.get("pyfly.web.host", "0.0.0.0"))
    _pyfly._port = int(_pyfly.config.get("pyfly.server.port", 8400))
    await _pyfly.startup()
    yield
    await _pyfly.shutdown()


app = create_app(
    title="flydesk-idp",
    version="0.1.0",
    description="Firefly Desk IDP -- multimodal document extraction with bounding boxes.",
    context=_pyfly.context,
    docs_enabled=True,
    actuator_enabled=True,
    lifespan=_lifespan,
)
