# Copyright 2026 Firefly Software Solutions Inc
"""``TransformationEngine`` -- dispatcher for the ``transform`` stage.

Walks the per-request list of transformations, picks the right
backend (declarative or LLM) based on the discriminator, and applies
each transformation per its declared ``scope``:

* ``scope=task``    -> mutate that task's groups in place. The result
  is read out of ``task.extracted_groups`` by downstream stages.
* ``scope=request`` -> concatenate the matching groups across every
  task, apply the transformation once, and append the result to a
  request-level list the orchestrator returns separately. Per-task
  groups stay untouched.

Failures degrade. A single bad transformation logs a warning and the
others still run.
"""

from __future__ import annotations

import logging

from pyfly.container import service

from flydesk_idp.core.services.transformations.entity_resolution import (
    EntityResolutionTransformer,
)
from flydesk_idp.core.services.transformations.llm_transformer import LlmTransformer
from flydesk_idp.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup
from flydesk_idp.interfaces.dtos.transformation import (
    EntityResolutionTransformation,
    LlmTransformation,
)

logger = logging.getLogger(__name__)


@service
class TransformationEngine:
    """Apply :class:`Transformation` objects to extracted groups.

    Both dependencies are autowired by type:

    * :class:`EntityResolutionTransformer` is itself ``@service``-decorated.
    * :class:`LlmTransformer` is registered as a ``@bean`` by
      :class:`IDPCoreConfiguration` because its constructor needs the
      ``transform`` prompt template + the default model — values
      pyfly cannot autoresolve by type alone.

    Picking ``@service`` over ``@bean`` keeps the wiring at the
    declaration site rather than in the central configuration file,
    which is the pyfly idiom for services whose dependencies are
    themselves DI-managed.
    """

    def __init__(
        self,
        *,
        entity_resolver: EntityResolutionTransformer,
        llm_transformer: LlmTransformer,
    ) -> None:
        self._entity = entity_resolver
        self._llm = llm_transformer

    async def apply_to_task(
        self,
        transformation,  # noqa: ANN001 -- discriminated union
        groups: list[ExtractedFieldGroup],
    ) -> ExtractedFieldGroup | None:
        """Apply a single transformation to one task's groups, in place."""
        try:
            return await self._dispatch(transformation, groups)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "transformation %s (%s) failed on task scope: %s",
                getattr(transformation, "id", "?"),
                getattr(transformation, "type", "?"),
                exc,
            )
            return None

    async def apply_request_scope(
        self,
        transformation,  # noqa: ANN001 -- discriminated union
        per_task_groups: list[list[ExtractedFieldGroup]],
    ) -> ExtractedFieldGroup | None:
        """Apply across every task. Return one consolidated group.

        Concatenates the rows of every matching ``target_group`` across
        tasks into a single synthetic group, applies the transformation
        to it, and returns the result. Per-task groups are NOT mutated
        by this path.
        """
        target_name = transformation.target_group
        consolidated = _consolidate_groups(per_task_groups, target_name)
        if consolidated is None:
            return None
        # Wrap the consolidated group in a one-element list so the
        # downstream transformers can operate on the same shape they
        # see in ``apply_to_task``. The synthetic group's output name
        # defaults to the transformation's ``output_group``, falling
        # back to ``target_group`` so request-scope output is always
        # distinguishable from the per-task groups.
        working = [consolidated]
        # Force a non-null output_group on the request scope so the
        # synthetic group is never silently merged with the original.
        original_output = transformation.output_group
        try:
            if original_output is None:
                transformation.output_group = target_name
            result = await self._dispatch(transformation, working)
        finally:
            transformation.output_group = original_output

        # ``_dispatch`` appended the produced group to ``working``;
        # locate it (last element, by construction).
        return working[-1] if len(working) > 1 else result

    # ------------------------------------------------------------------

    async def _dispatch(
        self,
        transformation,  # noqa: ANN001
        groups: list[ExtractedFieldGroup],
    ) -> ExtractedFieldGroup | None:
        if isinstance(transformation, EntityResolutionTransformation):
            return self._entity.apply(transformation, groups)
        if isinstance(transformation, LlmTransformation):
            return await self._llm.apply(transformation, groups)
        logger.warning(
            "transformation %s: unsupported type %r -- skipping",
            getattr(transformation, "id", "?"),
            type(transformation).__name__,
        )
        return None


def _consolidate_groups(
    per_task_groups: list[list[ExtractedFieldGroup]], target_name: str
) -> ExtractedFieldGroup | None:
    """Concat rows of every matching target group across tasks.

    Returns a synthetic :class:`ExtractedFieldGroup` whose single array
    field contains the union of rows. ``None`` when no task has the
    target group.
    """
    array_field_name = ""
    all_rows: list[ExtractedField] = []
    found_any = False
    for task_groups in per_task_groups:
        for g in task_groups:
            if g.fieldGroupName != target_name:
                continue
            found_any = True
            for f in g.fieldGroupFields:
                if isinstance(f.fieldValueFound, list):
                    if not array_field_name:
                        array_field_name = f.fieldName
                    all_rows.extend(r for r in f.fieldValueFound if isinstance(r, ExtractedField))
    if not found_any or not all_rows:
        return None
    array_field = ExtractedField(
        name=array_field_name or "rows",
        value=all_rows,
    )
    return ExtractedFieldGroup(
        fieldGroupName=target_name,
        fieldGroupFields=[array_field],
    )


__all__ = ["TransformationEngine"]
