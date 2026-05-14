# Copyright 2026 Firefly Software Solutions Inc
"""YAML prompt template resources.

Each ``*.yaml`` in this package is loaded at boot by
:class:`flydesk_idp.core.services.extraction.prompts.PromptCatalog`,
turned into a :class:`fireflyframework_agentic.prompts.PromptTemplate`,
and registered with the framework-wide :class:`PromptRegistry` so it
can be retrieved by name + version anywhere in the codebase.
"""
