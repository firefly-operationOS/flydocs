# Copyright 2026 Firefly Software Solutions Inc
"""``PromptCatalog`` -- single source of truth for every LLM prompt.

Templates live as YAML files under
:mod:`flydocs.resources.prompts`. At boot the catalog uses
``fireflyframework-agentic`` :class:`PromptLoader` to read each file,
instantiate a :class:`PromptTemplate`, and register it with the
framework-wide :class:`PromptRegistry`. This means:

* prompt text is plain YAML -- editable without touching Python,
* the framework registry can look any template up by ``name`` +
  ``version`` from anywhere,
* the catalog is a normal pyfly bean (declared in
  :class:`IDPCoreConfiguration`), so consumers receive the templates
  they need through constructor injection -- no module-level imports
  of prompt globals.
"""

from __future__ import annotations

import logging
from importlib import resources
from pathlib import Path

from fireflyframework_agentic.prompts import (
    PromptLoader,
    PromptRegistry,
    PromptTemplate,
    prompt_registry,
)

logger = logging.getLogger(__name__)


_PROMPT_FILES: dict[str, str] = {
    "extract": "extract.yaml",
    "extract_retry_arrays": "extract_retry_arrays.yaml",
    "splitter": "splitter.yaml",
    "classifier": "classifier.yaml",
    "content_authenticity": "content_authenticity.yaml",
    "visual_authenticity": "visual_authenticity.yaml",
    "judge": "judge.yaml",
    "rule_engine": "rule_engine.yaml",
    "bbox_matcher": "bbox_matcher.yaml",
    "transform": "transform.yaml",
}


class PromptCatalog:
    """Loaded prompts, indexed by short stage name.

    Use the named accessors (``catalog.extract``, ``catalog.judge``,
    ...) for the LLM stages that ship with flydocs. For ad-hoc
    lookups (e.g. user-supplied prompt overrides), use :meth:`get` --
    it delegates to the framework's :class:`PromptRegistry` so an
    application that registers extra templates can pull them out the
    same way.
    """

    def __init__(
        self,
        templates: dict[str, PromptTemplate],
        *,
        registry: PromptRegistry,
    ) -> None:
        self._templates = templates
        self._registry = registry

    # -- Named accessors -------------------------------------------------

    @property
    def extract(self) -> PromptTemplate:
        return self._templates["extract"]

    @property
    def extract_retry_arrays(self) -> PromptTemplate:
        return self._templates["extract_retry_arrays"]

    @property
    def splitter(self) -> PromptTemplate:
        return self._templates["splitter"]

    @property
    def classifier(self) -> PromptTemplate:
        return self._templates["classifier"]

    @property
    def content_authenticity(self) -> PromptTemplate:
        return self._templates["content_authenticity"]

    @property
    def visual_authenticity(self) -> PromptTemplate:
        return self._templates["visual_authenticity"]

    @property
    def judge(self) -> PromptTemplate:
        return self._templates["judge"]

    @property
    def rule_engine(self) -> PromptTemplate:
        return self._templates["rule_engine"]

    @property
    def bbox_matcher(self) -> PromptTemplate:
        return self._templates["bbox_matcher"]

    @property
    def transform(self) -> PromptTemplate:
        return self._templates["transform"]

    # -- Generic lookup --------------------------------------------------

    def get(self, name: str, version: str | None = None) -> PromptTemplate:
        """Look a template up in the framework registry by name + version."""
        return self._registry.get(name, version)

    def names(self) -> list[str]:
        """Short stage names of the templates bundled with this service."""
        return list(self._templates.keys())

    # -- Factory ---------------------------------------------------------

    @classmethod
    def from_resources(
        cls,
        *,
        registry: PromptRegistry | None = None,
    ) -> PromptCatalog:
        """Load every shipped template from ``resources/prompts`` and register it."""
        registry = registry or prompt_registry
        templates: dict[str, PromptTemplate] = {}
        prompts_dir = _resources_dir()
        for stage, filename in _PROMPT_FILES.items():
            template = PromptLoader.from_file(prompts_dir / filename)
            registry.register(template)
            templates[stage] = template
            logger.debug(
                "Registered prompt %s (stage=%s, version=%s)", template.name, stage, template.version
            )
        return cls(templates, registry=registry)


def _resources_dir() -> Path:
    """Resolve the bundled prompts directory as a filesystem path."""
    return Path(str(resources.files("flydocs.resources.prompts")))
