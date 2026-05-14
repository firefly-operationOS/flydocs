# Copyright 2026 Firefly Software Solutions Inc
"""End-to-end tests that hit a real LLM provider.

Opt-in: these tests are tagged with ``@pytest.mark.llm`` and skipped
by default (see the ``addopts = -m 'not llm'`` in ``pyproject.toml``).
Export ``ANTHROPIC_API_KEY`` (or another provider key) and run
``task test:llm`` to exercise them against the real model.
"""
