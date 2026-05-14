# Copyright 2026 Firefly Software Solutions Inc
"""Tiny ``/api/v1/version`` endpoint exposing build + model info."""

from __future__ import annotations

from pyfly.container import rest_controller
from pyfly.web import get_mapping, request_mapping

from flydesk_idp import __version__
from flydesk_idp.config import IDPSettings


@rest_controller
@request_mapping("/api/v1")
class VersionController:
    def __init__(self, settings: IDPSettings) -> None:
        self._settings = settings

    @get_mapping("/version")
    async def version(self) -> dict[str, str]:
        return {
            "service": "flydesk-idp",
            "version": __version__,
            "model": self._settings.model,
            "fallback_model": self._settings.fallback_model or "",
            "eda_adapter": self._settings.eda_adapter,
        }
