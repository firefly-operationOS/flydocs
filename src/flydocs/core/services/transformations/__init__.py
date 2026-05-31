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

from flydocs.core.services.transformations.entity_resolution import (
    EntityResolutionTransformer,
)
from flydocs.core.services.transformations.llm_transformer import LlmTransformer
from flydocs.core.services.transformations.transformation_engine import (
    TransformationEngine,
)

__all__ = [
    "EntityResolutionTransformer",
    "LlmTransformer",
    "TransformationEngine",
]
