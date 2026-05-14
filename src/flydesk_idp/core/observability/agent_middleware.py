# Copyright 2026 Firefly Software Solutions Inc
"""Shared agent middleware attached to every IDP :class:`FireflyAgent`.

Right now this is exclusively the Anthropic prompt-cache middleware.
We pre-build a single instance and re-use it across every service so
the cache settings stay consistent (same TTL, same blocks marked) --
the middleware is stateless, only writes ``cache_control`` markers
into ``kwargs["model_settings"]`` so the underlying pydantic-ai
Anthropic provider injects the cache breakpoints.

Anthropic prompt caching is documented here:
https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching

Cached blocks for our pipeline:

* ``cache_system_prompt=True`` -- caches the system prompt block of
  every agent. Each of our LLM services (splitter, classifier,
  extractor, judge, visual, content, rule_engine) is called many
  times per request with an identical system prompt, so this is the
  cheapest, highest-leverage win.
* ``cache_last_message=True`` -- caches the last user-message block.
  Helpful when an agent is invoked multiple times within the cache
  TTL with the same trailing content (e.g. a fixed document chunk).
"""

from __future__ import annotations

from fireflyframework_agentic.agents.prompt_cache import PromptCacheMiddleware

#: A single shared instance reused by every service. Module-level so
#: the agent factories don't need DI plumbing for what is effectively
#: configuration.
PROMPT_CACHE_MIDDLEWARE = PromptCacheMiddleware(
    cache_system_prompt=True,
    cache_last_message=True,
    cache_ttl_seconds=300,
)

#: Default middleware list passed to every FireflyAgent constructed
#: inside an IDP service. New cross-cutting middleware (auditing,
#: budget guards, etc.) is added here once and picked up everywhere.
DEFAULT_MIDDLEWARE = [PROMPT_CACHE_MIDDLEWARE]
