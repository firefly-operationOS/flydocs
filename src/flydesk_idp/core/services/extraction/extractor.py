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
from flydesk_idp.interfaces.dtos.field import ExtractedField, ExtractedFieldGroup
from flydesk_idp.interfaces.enums.field_type import FieldType

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
        agent_name: str = "flydesk-idp-extractor",
    ) -> None:
        self._template = template
        self._retry_arrays_template = retry_arrays_template
        self._model = model
        self._fallback_model = fallback_model
        self._agent_name = agent_name

    # ------------------------------------------------------------------
    # Array-empty retry parameters
    # ------------------------------------------------------------------
    # When a docspec declares array fields and the first extraction pass
    # returns ``rows=[]`` for one or more of them on a multi-page document,
    # we re-call the LLM ONCE with a focused "you missed these arrays"
    # prompt. This corrects a well-known structured-output failure mode
    # where Anthropic models default arrays to empty under verbose
    # schemas or generic intentions. The threshold + counter live here
    # so consumers can tune them via subclassing if needed.
    _ARRAY_RETRY_MIN_PAGES = 3
    _ARRAY_RETRY_MAX_ATTEMPTS = 1

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
                    doc.docType.documentType,
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
        doc: DocSpec,
        base_intention: str,
        empty_arrays: list[str],
        language_hint: str | None,
        model: str | None,
    ) -> tuple[list[ExtractedFieldGroup], str]:
        """Re-run extraction once with the retry-arrays system + user prompt.

        Uses the dedicated :pyattr:`_retry_arrays_template` (registered
        in ``PromptCatalog`` as ``flydesk_idp/extract_retry_arrays``)
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
        content: list[Any] = [
            user_text,
            BinaryContent(data=document_bytes, media_type=media_type),
        ]
        result = await timed_agent_run(agent, content, op="extract.retry_arrays", model=model_id)
        return normalise_doc(result.output, doc), model_id

    async def _extract_once(
        self,
        *,
        document_bytes: bytes,
        media_type: str,
        page_count: int,
        doc: DocSpec,
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
        content: list[Any] = [
            prompt.user,
            BinaryContent(data=document_bytes, media_type=media_type),
        ]
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
    # Empty-array detection & retry helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _suspicious_empty_arrays(doc: DocSpec, groups: list[ExtractedFieldGroup]) -> list[str]:
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
            f.fieldName for g in doc.fieldGroups for f in g.fieldGroupFields if f.fieldType == FieldType.ARRAY
        }
        if not array_field_names:
            return []
        empty: list[str] = []
        any_filled = False
        for group in groups:
            for field in group.fieldGroupFields:
                if field.fieldName not in array_field_names:
                    continue
                value = field.fieldValueFound
                if not isinstance(value, list):
                    continue
                if value:
                    any_filled = True
                else:
                    empty.append(field.fieldName)
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
        retry_by_group: dict[str, ExtractedFieldGroup] = {g.fieldGroupName: g for g in retry}
        merged: list[ExtractedFieldGroup] = []
        for orig_group in original:
            retry_group = retry_by_group.get(orig_group.fieldGroupName)
            if retry_group is None:
                merged.append(orig_group)
                continue
            retry_fields_by_name: dict[str, ExtractedField] = {
                f.fieldName: f for f in retry_group.fieldGroupFields
            }
            new_fields: list[ExtractedField] = []
            for orig_field in orig_group.fieldGroupFields:
                if orig_field.fieldName in empty_arrays and orig_field.fieldName in retry_fields_by_name:
                    new_fields.append(retry_fields_by_name[orig_field.fieldName])
                else:
                    new_fields.append(orig_field)
            merged.append(
                ExtractedFieldGroup(
                    fieldGroupName=orig_group.fieldGroupName,
                    fieldGroupFields=new_fields,
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
            model_settings={"max_tokens": self._MAX_OUTPUT_TOKENS},
            auto_register=False,
        )

    # Empirically, Sonnet 4.6 (and other Anthropic models under
    # structured-output) start to *default array fields to ``[]``* when
    # the schema JSON in the prompt grows verbose. A docspec with long
    # paragraph-style ``fieldGroupDesc`` and multi-sentence
    # ``fieldDescription`` values triggers this safety fallback even on
    # documents that plainly contain matching rows. We compress the
    # schema we send to the LLM (without mutating the caller's docspec)
    # so descriptions stay informative but compact.
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

    def _schema_payload(self, doc: DocSpec) -> str:
        schema = {
            "documentType": doc.docType.documentType,
            "description": self._compress(doc.docType.description, self._SCHEMA_FIELD_DESC_MAX),
            "country": doc.docType.country,
            "fieldGroups": [self._compress_group(g) for g in doc.fieldGroups],
        }
        return json.dumps(schema, indent=2, ensure_ascii=False)

    def _compress_group(self, group: Any) -> dict[str, Any]:
        raw = group.model_dump(mode="json", exclude_none=True)
        raw["fieldGroupDesc"] = self._compress(raw.get("fieldGroupDesc"), self._SCHEMA_GROUP_DESC_MAX)
        for field in raw.get("fieldGroupFields", []):
            field["fieldDescription"] = self._compress(
                field.get("fieldDescription"), self._SCHEMA_FIELD_DESC_MAX
            )
            for item in field.get("items") or []:
                item["fieldDescription"] = self._compress(
                    item.get("fieldDescription"), self._SCHEMA_ITEM_DESC_MAX
                )
        return raw
