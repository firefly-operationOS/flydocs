# Copyright 2026 Firefly Software Solutions Inc
"""Replace FastAPI's auto-generated OpenAPI with pyfly's richer schema.

The pyfly FastAPI adapter registers every controller method behind a
single ``lazy_endpoint(request: Request)`` shim so the DI container
can resolve the controller bean on first hit. The side-effect is that
FastAPI's built-in OpenAPI introspector sees only that shim -- no
request body, no response model, no tags, no docstring.

This module bridges the gap. After the FastAPI app is built we install
a custom ``app.openapi`` callable that:

1. Collects per-route metadata from the original controller signatures
   via pyfly's :class:`ControllerRegistrar.collect_route_metadata`,
2. Renders the spec through pyfly's :class:`OpenAPIGenerator`,
3. Enriches the result with global tags (with descriptions) and the
   OpenAPI ``info`` block we want Swagger / ReDoc to display.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI

from pyfly.context.application_context import ApplicationContext
from pyfly.web.adapters.starlette.controller import ControllerRegistrar
from pyfly.web.openapi import OpenAPIGenerator

logger = logging.getLogger(__name__)


#: Per-tag descriptions shown on the Swagger landing page.
TAG_DESCRIPTIONS: dict[str, str] = {
    "Extract": (
        "Synchronous extraction. One HTTP call runs the full multimodal "
        "pipeline (extract + validate + judge + business rules) and "
        "returns the assembled ExtractionResult."
    ),
    "Jobs": (
        "Asynchronous, queue-backed extraction. Submit a job, poll its "
        "status, fetch the result, or cancel it before the worker picks "
        "it up. Webhooks fire when the job leaves a terminal state."
    ),
    "Version": (
        "Service identity, primary / fallback model, and EDA adapter -- "
        "useful for smoke tests and operations dashboards."
    ),
}


def install_openapi(
    app: FastAPI,
    context: ApplicationContext,
    *,
    title: str,
    version: str,
    description: str,
) -> None:
    """Replace ``app.openapi`` with a pyfly-driven generator.

    Cached after the first call -- FastAPI's own ``openapi()`` method
    caches via ``app.openapi_schema`` and our override follows the
    same contract.
    """
    registrar = ControllerRegistrar()
    generator = OpenAPIGenerator(title=title, version=version, description=description)

    def _custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        route_metadata = registrar.collect_route_metadata(context)
        spec = generator.generate(route_metadata=route_metadata)

        # Enrich tag entries with human-readable descriptions.
        if spec.get("tags"):
            for tag in spec["tags"]:
                name = tag.get("name")
                if name and name in TAG_DESCRIPTIONS:
                    tag["description"] = TAG_DESCRIPTIONS[name]

        # Surface the deployment's primary endpoints in the info block.
        info = spec.setdefault("info", {})
        info.setdefault("contact", {"name": "Firefly OperationOS", "url": "https://github.com/firefly-operationOS"})

        app.openapi_schema = spec
        logger.info(
            "openapi schema generated (paths=%d, schemas=%d, tags=%d)",
            len(spec.get("paths", {})),
            len((spec.get("components") or {}).get("schemas", {})),
            len(spec.get("tags", [])),
        )
        return spec

    app.openapi = _custom_openapi  # type: ignore[method-assign]
