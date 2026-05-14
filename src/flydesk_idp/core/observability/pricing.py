# Copyright 2026 Firefly Software Solutions Inc
"""LLM price-table overrides for models the framework doesn't ship.

``fireflyframework_agentic.observability.cost`` carries a built-in
:class:`StaticPriceCostCalculator` with prices for the major models,
but newer model ids (``anthropic:claude-opus-4-7``, ``claude-sonnet-4-6``,
``claude-haiku-4-5``) are not in the table yet, so cost estimates come
back as ``$0.00``. The fix is to extend the module-level price table at
boot so every fresh calculator instance picks up our overrides.

Prices are USD per 1 M tokens, tuple of ``(input_per_M, output_per_M)``,
matching the framework's existing dict layout. Values reflect public
Anthropic pricing for the Claude 4 family.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# USD / 1M tokens. Source: Anthropic public pricing for the Claude 4
# family (Opus, Sonnet, Haiku). Update here when prices change; the
# framework will pick up overrides automatically on the next call.
_FLYDESK_PRICE_OVERRIDES: dict[str, tuple[float, float]] = {
    # Opus tier
    "anthropic:claude-opus-4-7":          (15.00, 75.00),
    "anthropic:claude-opus-4-6":          (15.00, 75.00),
    "anthropic:claude-opus-4-5":          (15.00, 75.00),
    "anthropic:claude-opus-4":            (15.00, 75.00),
    # Sonnet tier
    "anthropic:claude-sonnet-4-6":        (3.00, 15.00),
    "anthropic:claude-sonnet-4-5":        (3.00, 15.00),
    "anthropic:claude-sonnet-4":          (3.00, 15.00),
    # Haiku tier
    "anthropic:claude-haiku-4-5":         (0.80, 4.00),
    "anthropic:claude-haiku-4-5-20251001":(0.80, 4.00),
    "anthropic:claude-haiku-4":           (0.80, 4.00),
}


def install_price_overrides() -> None:
    """Merge our overrides into the framework's default price table.

    Safe to call multiple times: the framework's table is a module-level
    dict and we just ``.update()`` it, so re-running is a no-op when the
    keys are already present.
    """
    try:
        from fireflyframework_agentic.observability import cost as _cost_mod
    except Exception:  # noqa: BLE001
        logger.warning("fireflyframework_agentic.observability.cost not importable; price overrides skipped")
        return
    _cost_mod._DEFAULT_PRICES.update(_FLYDESK_PRICE_OVERRIDES)  # noqa: SLF001
    logger.info(
        "Installed %d flydesk-idp LLM price overrides",
        len(_FLYDESK_PRICE_OVERRIDES),
    )
