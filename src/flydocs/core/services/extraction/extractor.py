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

"""``MultimodalExtractor`` -- one LLM call per :class:`DocumentTypeSpec`.

The extractor produces both **fields and bounding boxes** in one shot;
there is no separate bbox-finder stage. Document bytes are shipped
straight to the LLM through :class:`BinaryContent`, so PDF, image, and
any other format the provider accepts all flow through the same path.

The prompt template is injected through the pyfly DI container -- the
service never imports a template directly. This keeps the LLM stages
swappable per deployment (you can register a different
``flydocs/extract`` version in the framework registry without
touching the extractor's source).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fireflyframework_agentic.agents import FireflyAgent
from fireflyframework_agentic.prompts import PromptTemplate
from fireflyframework_agentic.types import BinaryContent

from flydocs.core.observability import DEFAULT_MIDDLEWARE, IDP_MODEL_SETTINGS, timed_agent_run
from flydocs.core.services.extraction.postprocess import normalise_doc
from flydocs.core.services.extraction.schema import build_extraction_output_model
from flydocs.core.services.extraction.text_anchor import NoOpTextAnchor, TextAnchor
from flydocs.interfaces.dtos.document_type import DocumentTypeSpec
from flydocs.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup
from flydocs.interfaces.enums.field_type import FieldType

logger = logging.getLogger(__name__)


class MultimodalExtractor:
    """Multimodal IDP extractor."""

    def __init__(
        self,
        *,
        template: PromptTemplate,
        retry_arrays_template: PromptTemplate | None = None,
        model: str,
        fallback_model: str | None = None,
        agent_name: str = "flydocs-extractor",
        text_anchor: TextAnchor | None = None,
    ) -> None:
        self._template = template
        self._retry_arrays_template = retry_arrays_template
        self._model = model
        self._fallback_model = fallback_model
        self._agent_name = agent_name
        # When ``text_anchor`` returns a non-empty string the extractor
        # splices it into the user message ahead of the binary content,
        # giving the LLM two modalities to cross-reference. The default
        # :class:`NoOpTextAnchor` returns ``None`` so the slim image
        # without the optional dep keeps the binary-only behaviour.
        self._text_anchor: TextAnchor = text_anchor or NoOpTextAnchor()

    # ------------------------------------------------------------------
    # Array-empty retry parameters
    # ------------------------------------------------------------------
    # When a document type declares array fields and the first extraction
    # pass returns ``rows=[]`` for one or more of them on a multi-page
    # document, we re-call the LLM ONCE with a focused "you missed these
    # arrays" prompt. This corrects a well-known structured-output
    # failure mode where Anthropic models default arrays to empty under
    # verbose schemas or generic intentions. The threshold + counter
    # live here so consumers can tune them via subclassing if needed.
    _ARRAY_RETRY_MIN_PAGES = 3
    _ARRAY_RETRY_MAX_ATTEMPTS = 1

    async def extract(
        self,
        *,
        document_bytes: bytes,
        media_type: str,
        page_count: int,
        doc: DocumentTypeSpec,
        intention: str,
        language_hint: str | None = None,
        model: str | None = None,
    ) -> tuple[list[ExtractedFieldGroup], str]:
        """Run one extraction call, returning ``(groups, model_actually_used)``.

        If the LLM returns ``rows=[]`` for any array field on a document
        with at least :pyattr:`_ARRAY_RETRY_MIN_PAGES` pages, the method
        re-runs the extraction once with a focused retry intention. This
        masks a known structured-output failure mode where complex
        schemas + generic intentions cause the model to default array
        fields to empty as a "safe" output.
        """
        groups, model_used = await self._extract_once(
            document_bytes=document_bytes,
            media_type=media_type,
            page_count=page_count,
            doc=doc,
            intention=intention,
            language_hint=language_hint,
            model=model,
            op="extract",
        )
        if self._retry_arrays_template is not None and page_count >= self._ARRAY_RETRY_MIN_PAGES:
            empty_arrays = self._suspicious_empty_arrays(doc, groups)
            if empty_arrays:
                logger.info(
                    "extract.empty_array_retry triggered: doc=%s pages=%d empty=%s",
                    doc.id,
                    page_count,
                    empty_arrays,
                )
                retry_groups, retry_model = await self._extract_retry_arrays(
                    document_bytes=document_bytes,
                    media_type=media_type,
                    page_count=page_count,
                    doc=doc,
                    base_intention=intention,
                    empty_arrays=empty_arrays,
                    language_hint=language_hint,
                    model=model,
                )
                groups = self._merge_after_retry(
                    original=groups, retry=retry_groups, empty_arrays=empty_arrays
                )
                model_used = retry_model
        return groups, model_used

    async def _extract_retry_arrays(
        self,
        *,
        document_bytes: bytes,
        media_type: str,
        page_count: int,
        doc: DocumentTypeSpec,
        base_intention: str,
        empty_arrays: list[str],
        language_hint: str | None,
        model: str | None,
    ) -> tuple[list[ExtractedFieldGroup], str]:
        """Re-run extraction once with the retry-arrays system + user prompt.

        Uses the dedicated :pyattr:`_retry_arrays_template` (registered
        in ``PromptCatalog`` as ``flydocs/extract_retry_arrays``)
        rather than building intention strings inline -- keeps prompt
        text editable in YAML and uniformly in English.
        """
        assert self._retry_arrays_template is not None
        retry_prompt = self._retry_arrays_template.render(
            empty_array_names=", ".join(f"``{n}``" for n in empty_arrays),
            page_count=page_count,
        )
        model_id = model or self._model
        output_model = build_extraction_output_model(doc)
        agent = self._build_agent(model_id, output_model, instructions=retry_prompt.system)
        # Append the schema JSON so the LLM still knows the contract,
        # but keep the user prompt itself short and action-oriented --
        # verbose retry prompts have been observed to re-trigger the
        # same empty-array default we are trying to recover from.
        schema_json = self._schema_payload(doc)
        user_text = f"{retry_prompt.user.strip()}\n\nSchema:\n```json\n{schema_json}\n```"
        content = self._build_user_content(
            user_text=user_text,
            document_bytes=document_bytes,
            media_type=media_type,
        )
        result = await timed_agent_run(agent, content, op="extract.retry_arrays", model=model_id)
        return normalise_doc(result.output, doc), model_id

    async def _extract_once(
        self,
        *,
        document_bytes: bytes,
        media_type: str,
        page_count: int,
        doc: DocumentTypeSpec,
        intention: str,
        language_hint: str | None,
        model: str | None,
        op: str,
    ) -> tuple[list[ExtractedFieldGroup], str]:
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
        content = self._build_user_content(
            user_text=prompt.user,
            document_bytes=document_bytes,
            media_type=media_type,
        )
        try:
            result = await timed_agent_run(agent, content, op=op, model=model_id)
            return normalise_doc(result.output, doc), model_id
        except Exception as exc:  # noqa: BLE001
            if not self._fallback_model or self._fallback_model == model_id:
                raise
            logger.warning(
                "Primary model %s failed (%s); retrying on fallback %s",
                model_id,
                exc,
                self._fallback_model,
            )
            fallback_agent = self._build_agent(self._fallback_model, output_model, instructions=prompt.system)
            result = await timed_agent_run(
                fallback_agent, content, op=f"{op}.fallback", model=self._fallback_model
            )
            return normalise_doc(result.output, doc), self._fallback_model

    # ------------------------------------------------------------------
    # User content composition (text anchor + binary)
    # ------------------------------------------------------------------

    def _build_user_content(
        self,
        *,
        user_text: str,
        document_bytes: bytes,
        media_type: str,
    ) -> list[Any]:
        """Compose the user-side multimodal content block list.

        Layout: ``[user_text, (optional anchor), BinaryContent]``. The
        anchor sits between the instruction text and the binary so the
        model sees the instruction, the cleaned-up textual view of the
        document, and finally the raw bytes -- in that order.
        """
        content: list[Any] = [user_text]
        anchor_text = self._render_anchor(document_bytes, media_type)
        if anchor_text:
            content.append(anchor_text)
        content.append(BinaryContent(data=document_bytes, media_type=media_type))
        return content

    def _render_anchor(self, document_bytes: bytes, media_type: str) -> str | None:
        try:
            rendered = self._text_anchor.produce(document_bytes, media_type=media_type)
        except Exception as exc:  # noqa: BLE001 -- never block extract on a degraded anchor
            logger.warning("text-anchor produce() raised %s; continuing without anchor", exc)
            return None
        if not rendered:
            return None
        # Fence the anchor so the model recognises it as a derived view,
        # not part of its own instructions.
        return (
            "Text-layer anchor (Docling pre-extraction, for cross-reference only):\n"
            "```markdown\n"
            f"{rendered}\n"
            "```"
        )

    # ------------------------------------------------------------------
    # Empty-array detection & retry helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _suspicious_empty_arrays(doc: DocumentTypeSpec, groups: list[ExtractedFieldGroup]) -> list[str]:
        """Names of array fields that look like a structured-output
        empty-default rather than a legitimately empty result.

        Heuristic: a single empty array side-by-side with other arrays
        that **did** fill rows means the model engaged with the schema
        and judged this particular array to be empty -- trust it. Only
        when **every** array field in the document came back empty do
        we treat the response as suspicious and worth retrying (that's
        the signature of the model defaulting all arrays for safety,
        not a genuine zero-evidence document).
        """
        array_field_names: set[str] = {
            f.name for g in doc.field_groups for f in g.fields if f.type == FieldType.ARRAY
        }
        if not array_field_names:
            return []
        empty: list[str] = []
        any_filled = False
        for group in groups:
            for field in group.fields:
                if field.name not in array_field_names:
                    continue
                value = field.value
                if not isinstance(value, list):
                    continue
                if value:
                    any_filled = True
                else:
                    empty.append(field.name)
        # A filled sibling array means the model engaged with the
        # schema; the empty ones are genuine zero-evidence results.
        if any_filled:
            return []
        return empty

    @staticmethod
    def _merge_after_retry(
        *,
        original: list[ExtractedFieldGroup],
        retry: list[ExtractedFieldGroup],
        empty_arrays: list[str],
    ) -> list[ExtractedFieldGroup]:
        """Keep retry's results for the targeted array fields; preserve
        originals for everything else."""
        retry_by_group: dict[str, ExtractedFieldGroup] = {g.name: g for g in retry}
        merged: list[ExtractedFieldGroup] = []
        for orig_group in original:
            retry_group = retry_by_group.get(orig_group.name)
            if retry_group is None:
                merged.append(orig_group)
                continue
            retry_fields_by_name: dict[str, ExtractedField] = {f.name: f for f in retry_group.fields}
            new_fields: list[ExtractedField] = []
            for orig_field in orig_group.fields:
                if orig_field.name in empty_arrays and orig_field.name in retry_fields_by_name:
                    new_fields.append(retry_fields_by_name[orig_field.name])
                else:
                    new_fields.append(orig_field)
            merged.append(
                ExtractedFieldGroup(
                    name=orig_group.name,
                    fields=new_fields,
                )
            )
        return merged

    # ------------------------------------------------------------------

    # Output-token budget for the extractor. The default ``max_tokens=4096``
    # used by Anthropic's API is too tight for documents with large
    # array fields (multi-page personas / line_items): the LLM truncates
    # mid-array and pydantic-ai silently falls back to ``rows=[]``.
    # 8192 is the current public ceiling for Sonnet 4.6 and gives the
    # extractor room to emit 15-30 row arrays comfortably.
    _MAX_OUTPUT_TOKENS = 8192

    def _build_agent(self, model_id: str, output_model: type, *, instructions: str) -> FireflyAgent[Any, Any]:
        return FireflyAgent(
            name=self._agent_name,
            model=model_id,
            instructions=instructions,
            output_type=output_model,
            description="Multimodal IDP extractor",
            tags=["idp", "extractor"],
            middleware=list(DEFAULT_MIDDLEWARE),
            model_settings={**IDP_MODEL_SETTINGS, "max_tokens": self._MAX_OUTPUT_TOKENS},
            auto_register=False,
        )

    # Empirically, Sonnet 4.6 (and other Anthropic models under
    # structured-output) start to *default array fields to ``[]``* when
    # the schema JSON in the prompt grows verbose. A long ``description``
    # on the document type and multi-sentence field descriptions trigger
    # this safety fallback even on documents that plainly contain
    # matching rows. We compress the schema we send to the LLM (without
    # mutating the caller's spec) so descriptions stay informative but
    # compact.
    _SCHEMA_GROUP_DESC_MAX = 180
    _SCHEMA_FIELD_DESC_MAX = 160
    _SCHEMA_ITEM_DESC_MAX = 140

    @classmethod
    def _compress(cls, text: str | None, limit: int) -> str | None:
        if not text:
            return text
        cleaned = " ".join(text.split())
        if len(cleaned) <= limit:
            return cleaned
        # Cut on the nearest sentence boundary before the limit.
        cut = cleaned[:limit].rsplit(". ", 1)[0]
        return cut.rstrip(".") + "."

    def _schema_payload(self, doc: DocumentTypeSpec) -> str:
        schema = {
            "id": doc.id,
            "description": self._compress(doc.description, self._SCHEMA_FIELD_DESC_MAX),
            "country": doc.country,
            "field_groups": [self._compress_group(g) for g in doc.field_groups],
        }
        return json.dumps(schema, indent=2, ensure_ascii=False)

    def _compress_group(self, group: Any) -> dict[str, Any]:
        raw = group.model_dump(mode="json", exclude_none=True)
        raw["description"] = self._compress(raw.get("description"), self._SCHEMA_GROUP_DESC_MAX)
        for field in raw.get("fields", []) or []:
            field["description"] = self._compress(field.get("description"), self._SCHEMA_FIELD_DESC_MAX)
            items = field.get("items")
            if isinstance(items, dict):
                items["description"] = self._compress(items.get("description"), self._SCHEMA_ITEM_DESC_MAX)
                for sub in items.get("fields", []) or []:
                    sub["description"] = self._compress(sub.get("description"), self._SCHEMA_ITEM_DESC_MAX)
        return raw
