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

import importlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from flydocs.core.observability import (
    get_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)
from flydocs.core.services.pipeline import orchestrator as orch
from flydocs.interfaces.dtos.extract import TraceEntry, UsageBreakdown

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

    from flydocs.core.observability import outbound_log

    token = set_correlation_id("req-abc")
    try:
        await outbound_log.timed_agent_run(_FakeAgent(), [], op="extract", model="anthropic:claude-opus-4-7")
    finally:
        reset_correlation_id(token)
    assert captured["context"] is not None
    assert captured["context"].correlation_id == "req-abc"


# ---------------------------------------------------------------------------
# prompt cache toggle
# ---------------------------------------------------------------------------


def _reload_middleware_module() -> Any:
    """Force re-import so the module-level env var read is re-run."""
    from flydocs.core.observability import agent_middleware

    return importlib.reload(agent_middleware)


def test_prompt_cache_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env var set -> caching is on, one middleware in the list."""
    monkeypatch.delenv("FLYDOCS_PROMPT_CACHE", raising=False)
    mod = _reload_middleware_module()
    assert mod._prompt_cache_enabled() is True
    assert len(mod.DEFAULT_MIDDLEWARE) == 1


@pytest.mark.parametrize("value", ["off", "OFF", "0", "false", "False", "no"])
def test_prompt_cache_env_var_disables_middleware(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """``FLYDOCS_PROMPT_CACHE`` truthy-off values empty the middleware list."""
    monkeypatch.setenv("FLYDOCS_PROMPT_CACHE", value)
    mod = _reload_middleware_module()
    assert mod._prompt_cache_enabled() is False
    assert mod.DEFAULT_MIDDLEWARE == []


# ---------------------------------------------------------------------------
# cost resolution (delegated to genai-prices via the framework)
# ---------------------------------------------------------------------------


def test_genai_prices_resolves_our_anthropic_models() -> None:
    """The framework's resolver chain hits ``genai-prices`` for our model ids.

    Asserts the live price database knows about Claude 4 Opus/Sonnet/Haiku --
    if a release shuffles those entries the test fails loudly so the
    response stops reporting ``$0.00`` silently.

    Skipped when the framework's cost module isn't reachable on the
    Python path. The cost telemetry feature is itself optional --
    :func:`flydocs.core.observability.outbound_log._extract_usage_fields`
    already swallows the same ImportError silently -- so skipping here
    only loses test coverage, not service behaviour.
    """
    # The cost helpers live on the framework's
    # ``observability.cost_resolvers`` module, with
    # ``observability.cost`` as a secondary import path. Try both; skip
    # only when neither is present (the cost feature itself is optional --
    # the production extractor swallows the same ImportError silently).
    try:
        from fireflyframework_agentic.observability.cost_resolvers import (  # type: ignore[no-redef]
            CostContext,
            genai_prices_cost,
        )
    except ImportError:
        try:
            from fireflyframework_agentic.observability.cost import (  # type: ignore[no-redef]
                CostContext,
                genai_prices_cost,
            )
        except ImportError:
            pytest.skip("cost helpers not exported on this fireflyframework-agentic ref")

    # 1 M input + 1 M output. Exact numbers are not asserted (genai-prices
    # tracks the live tariff and changes over time); we only require that
    # the lookup succeeds and returns something material.
    for model in (
        "anthropic:claude-opus-4-7",
        "anthropic:claude-sonnet-4-6",
        "anthropic:claude-haiku-4-5",
    ):
        cost = genai_prices_cost(
            CostContext(
                model=model,
                input_tokens=1_000_000,
                output_tokens=1_000_000,
            )
        )
        assert cost is not None, f"genai-prices does not know about {model}"
        assert cost > 1.0, f"{model} priced absurdly cheap: ${cost}"


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
            "flydocs-extract": {
                "input_tokens": 700,
                "output_tokens": 350,
                "total_tokens": 1050,
                "cost_usd": 0.30,
                "requests": 2,
            },
            "flydocs-judge": {
                "input_tokens": 300,
                "output_tokens": 150,
                "total_tokens": 450,
                "cost_usd": 0.12,
                "requests": 1,
            },
        },
        by_model={
            "anthropic:claude-opus-4-7": {
                "input_tokens": 1000,
                "output_tokens": 500,
                "total_tokens": 1500,
                "cost_usd": cost,
                "requests": record_count,
            }
        },
    )


def test_usage_breakdown_maps_pipeline_result_usage() -> None:
    """When the engine returns a usage summary, the helper maps it 1:1."""
    pipeline_result = SimpleNamespace(usage=_make_summary())
    breakdown = orch._usage_breakdown(request_id="req-1", pipeline_result=pipeline_result)
    assert isinstance(breakdown, UsageBreakdown)
    assert breakdown.total_tokens == 1500
    assert breakdown.total_cost_usd == pytest.approx(0.42)
    assert "flydocs-extract" in breakdown.by_agent
    assert "anthropic:claude-opus-4-7" in breakdown.by_model


def test_usage_breakdown_returns_none_when_no_records() -> None:
    pipeline_result = SimpleNamespace(usage=_make_summary(record_count=0))
    # The tracker fallback may still pick up unrelated records from
    # other tests -- a fresh correlation id should give an empty result.
    breakdown = orch._usage_breakdown(request_id="req-no-records-xyz", pipeline_result=pipeline_result)
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
