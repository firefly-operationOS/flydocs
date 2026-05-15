# Copyright 2026 Firefly Software Solutions Inc
"""Post-extraction transformations applied by the ``transform`` stage.

* :class:`EntityResolutionTransformer` -- deterministic dedup of array
  field group rows. Free, multilingual via NFKD + token-subset matching.
* :class:`LlmTransformer` -- free-form LLM call against the rows of a
  target group; the caller's ``intention`` drives any post-processing
  that does not fit a declarative type.
* :class:`TransformationEngine` -- dispatcher that picks the right
  transformer based on the DTO's discriminator and applies it to the
  task's extracted groups (or consolidates across tasks for
  ``scope=request``).
"""

from flydesk_idp.core.services.transformations.entity_resolution import (
    EntityResolutionTransformer,
)
from flydesk_idp.core.services.transformations.llm_transformer import LlmTransformer
from flydesk_idp.core.services.transformations.transformation_engine import (
    TransformationEngine,
)

__all__ = [
    "EntityResolutionTransformer",
    "LlmTransformer",
    "TransformationEngine",
]
