# Copyright 2026 Firefly Software Solutions Inc
"""``MultimodalExtractor`` -- one LLM call per :class:`DocSpec`.

The extractor produces both **fields and bounding boxes** in one shot;
there is no separate bbox-finder stage. Document bytes are shipped
straight to the LLM through :class:`BinaryContent`, so PDF, image, and
any other format the provider accepts all flow through the same path.

The prompt template is injected through the pyfly DI container -- the
service never imports a template directly. This keeps the LLM stages
swappable per deployment (you can register a different
``flydesk_idp/extract`` version in the framework registry without
touching the extractor's source).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fireflyframework_agentic.agents import FireflyAgent
from fireflyframework_agentic.prompts import PromptTemplate
from fireflyframework_agentic.types import BinaryContent

from flydesk_idp.core.observability import DEFAULT_MIDDLEWARE, timed_agent_run
from flydesk_idp.core.services.extraction.postprocess import normalise_doc
from flydesk_idp.core.services.extraction.schema import build_extraction_output_model
from flydesk_idp.interfaces.dtos.doc import DocSpec
from flydesk_idp.interfaces.dtos.field import ExtractedFieldGroup

logger = logging.getLogger(__name__)


class MultimodalExtractor:
    """Multimodal IDP extractor."""

    def __init__(
        self,
        *,
        template: PromptTemplate,
        model: str,
        fallback_model: str | None = None,
        agent_name: str = "flydesk-idp-extractor",
    ) -> None:
        self._template = template
        self._model = model
        self._fallback_model = fallback_model
        self._agent_name = agent_name

    async def extract(
        self,
        *,
        document_bytes: bytes,
        media_type: str,
        page_count: int,
        doc: DocSpec,
        intention: str,
        language_hint: str | None = None,
        model: str | None = None,
    ) -> tuple[list[ExtractedFieldGroup], str]:
        """Run one extraction call, returning ``(groups, model_actually_used)``."""
        model_id = model or self._model
        output_model = build_extraction_output_model(doc)
        schema_json = self._schema_payload(doc)
        prompt = self._template.render(
            schema_json=schema_json,
            media_type=media_type,
            page_count=page_count,
            intention=intention,
            language_hint=language_hint or "",
        )
        agent = self._build_agent(model_id, output_model, instructions=prompt.system)

        content: list[Any] = [
            prompt.user,
            BinaryContent(data=document_bytes, media_type=media_type),
        ]
        try:
            result = await timed_agent_run(agent, content, op="extract", model=model_id)
            return normalise_doc(result.output, doc), model_id
        except Exception as exc:  # noqa: BLE001
            if not self._fallback_model or self._fallback_model == model_id:
                raise
            logger.warning(
                "Primary model %s failed (%s); retrying on fallback %s",
                model_id, exc, self._fallback_model,
            )
            fallback_agent = self._build_agent(
                self._fallback_model, output_model, instructions=prompt.system
            )
            result = await timed_agent_run(
                fallback_agent, content, op="extract.fallback", model=self._fallback_model
            )
            return normalise_doc(result.output, doc), self._fallback_model

    # ------------------------------------------------------------------

    def _build_agent(
        self, model_id: str, output_model: type, *, instructions: str
    ) -> FireflyAgent[Any, Any]:
        return FireflyAgent(
            name=self._agent_name,
            model=model_id,
            instructions=instructions,
            output_type=output_model,
            description="Multimodal IDP extractor",
            tags=["idp", "extractor"],
            middleware=list(DEFAULT_MIDDLEWARE),
            auto_register=False,
        )

    def _schema_payload(self, doc: DocSpec) -> str:
        schema = {
            "documentType": doc.docType.documentType,
            "description": doc.docType.description,
            "country": doc.docType.country,
            "fieldGroups": [g.model_dump(mode="json", exclude_none=True) for g in doc.fieldGroups],
        }
        return json.dumps(schema, indent=2, ensure_ascii=False)
