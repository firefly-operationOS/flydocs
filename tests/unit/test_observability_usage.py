# Copyright 2026 Firefly Software Solutions Inc
"""Unit tests for the observability metadata surface.

Covers the three behaviours that surface ``usage`` + ``trace`` on the
``ExtractionResult``:

* ``timed_agent_run`` propagates the active correlation id into the
  agent's ``AgentContext`` so the framework's per-call ``UsageRecord``s
  are queryable by request.
* The orchestrator's helpers correctly map the framework's
  ``UsageSummary`` and ``ExecutionTraceEntry`` lists into our public
  DTOs.
* The price-table override pushes our model ids (``claude-opus-4-7``,
  ``claude-sonnet-4-6``, ``claude-haiku-4-5``) into the framework's
  calculator so cost > 0.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from flydesk_idp.core.observability import (
    get_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)
from flydesk_idp.core.observability.pricing import install_price_overrides
from flydesk_idp.core.services.pipeline import orchestrator as orch
from flydesk_idp.interfaces.dtos.extract import TraceEntry, UsageBreakdown


# ---------------------------------------------------------------------------
# correlation id contextvar
# ---------------------------------------------------------------------------


def test_correlation_id_default_is_none() -> None:
    assert get_correlation_id() is None


def test_correlation_id_set_reset_round_trip() -> None:
    token = set_correlation_id("req-123")
    try:
        assert get_correlation_id() == "req-123"
    finally:
        reset_correlation_id(token)
    assert get_correlation_id() is None


@pytest.mark.asyncio
async def test_correlation_id_propagates_into_agent_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``timed_agent_run`` must pass the active correlation id via
    ``AgentContext`` so the framework's usage tracker can scope records."""
    captured: dict[str, Any] = {}

    class _FakeAgent:
        async def run(self, content: Any, *, context: Any = None) -> Any:
            captured["context"] = context
            return SimpleNamespace(usage=lambda: None)

    from flydesk_idp.core.observability import outbound_log

    token = set_correlation_id("req-abc")
    try:
        await outbound_log.timed_agent_run(
            _FakeAgent(), [], op="extract", model="anthropic:claude-opus-4-7"
        )
    finally:
        reset_correlation_id(token)
    assert captured["context"] is not None
    assert captured["context"].correlation_id == "req-abc"


# ---------------------------------------------------------------------------
# price override
# ---------------------------------------------------------------------------


def test_price_override_covers_opus_sonnet_haiku() -> None:
    install_price_overrides()
    from fireflyframework_agentic.observability.cost import get_cost_calculator

    calc = get_cost_calculator("auto")
    # 1 M input + 1 M output -> (in + out) per-M dollars
    assert calc.estimate("anthropic:claude-opus-4-7", 1_000_000, 1_000_000) == pytest.approx(90.00)
    assert calc.estimate("anthropic:claude-sonnet-4-6", 1_000_000, 1_000_000) == pytest.approx(18.00)
    assert calc.estimate("anthropic:claude-haiku-4-5", 1_000_000, 1_000_000) == pytest.approx(4.80)


# ---------------------------------------------------------------------------
# orchestrator helpers
# ---------------------------------------------------------------------------


def _make_summary(*, record_count: int = 3, cost: float = 0.42) -> Any:
    """Build a ``UsageSummary`` look-alike that's good enough for the helper."""
    return SimpleNamespace(
        total_input_tokens=1000,
        total_output_tokens=500,
        total_tokens=1500,
        total_cost_usd=cost,
        total_requests=record_count,
        total_latency_ms=12345.0,
        record_count=record_count,
        by_agent={
            "flydesk-idp-extract": {
                "input_tokens": 700, "output_tokens": 350, "total_tokens": 1050,
                "cost_usd": 0.30, "requests": 2,
            },
            "flydesk-idp-judge": {
                "input_tokens": 300, "output_tokens": 150, "total_tokens": 450,
                "cost_usd": 0.12, "requests": 1,
            },
        },
        by_model={"anthropic:claude-opus-4-7": {
            "input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500,
            "cost_usd": cost, "requests": record_count,
        }},
    )


def test_usage_breakdown_maps_pipeline_result_usage() -> None:
    """When the engine returns a usage summary, the helper maps it 1:1."""
    pipeline_result = SimpleNamespace(usage=_make_summary())
    breakdown = orch._usage_breakdown(request_id="req-1", pipeline_result=pipeline_result)
    assert isinstance(breakdown, UsageBreakdown)
    assert breakdown.total_tokens == 1500
    assert breakdown.total_cost_usd == pytest.approx(0.42)
    assert "flydesk-idp-extract" in breakdown.by_agent
    assert "anthropic:claude-opus-4-7" in breakdown.by_model


def test_usage_breakdown_returns_none_when_no_records() -> None:
    pipeline_result = SimpleNamespace(usage=_make_summary(record_count=0))
    # The tracker fallback may still pick up unrelated records from
    # other tests -- a fresh correlation id should give an empty result.
    breakdown = orch._usage_breakdown(
        request_id="req-no-records-xyz", pipeline_result=pipeline_result
    )
    assert breakdown is None


def test_trace_entries_compute_latency_from_timestamps() -> None:
    t0 = datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC)
    t1 = t0 + timedelta(milliseconds=153)
    pipeline_result = SimpleNamespace(
        execution_trace=[
            SimpleNamespace(node_id="extract", started_at=t0, completed_at=t1, status="success"),
        ]
    )
    entries = orch._trace_entries(pipeline_result)
    assert len(entries) == 1
    assert isinstance(entries[0], TraceEntry)
    assert entries[0].node == "extract"
    assert entries[0].latency_ms == pytest.approx(153.0, abs=0.1)
    assert entries[0].status == "success"


def test_trace_entries_empty_when_no_pipeline_result() -> None:
    assert orch._trace_entries(None) == []
    assert orch._trace_entries(SimpleNamespace(execution_trace=[])) == []
