# Copyright 2026 Firefly Software Solutions Inc
"""``PipelineOrchestrator`` -- runs the IDP pipeline as a
:class:`fireflyframework_agentic.pipeline.PipelineEngine` DAG.

Each stage of the IDP flow (load, split, extract, validate, visual /
content authenticity, judge, rule engine, assemble) is a
:class:`CallableStep` added to a per-request DAG. Stages downstream of
the extractor are opt-in through :class:`StageToggles`; the DAG is
built fresh per request so the trace and event log reflect exactly
what ran for that call.

The orchestrator exposes :meth:`execute` (named ``execute`` rather than
``run`` so it does not accidentally satisfy pyfly's
``CommandLineRunner`` structural protocol).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fireflyframework_agentic.pipeline import (
    CallableStep,
    PipelineBuilder,
    PipelineContext,
)

from flydesk_idp.core.services.authenticity import (
    ContentAuthenticityChecker,
    VisualAuthenticityChecker,
)
from flydesk_idp.core.services.escalation import JudgeEscalator
from flydesk_idp.core.services.extraction.extractor import MultimodalExtractor
from flydesk_idp.core.services.extraction.loader import load_document
from flydesk_idp.core.services.extraction.pdf_slicer import PageRange, slice_pdf
from flydesk_idp.core.services.judge import Judge
from flydesk_idp.core.services.rules import RuleEngine
from flydesk_idp.core.services.splitting import DocumentSplitter, SplitDocument
from flydesk_idp.core.services.validation import FieldValidator
from flydesk_idp.interfaces.dtos.authenticity import (
    ContentAuthenticity,
    DocumentAuthenticity,
    VisualValidationOutcome,
)
from flydesk_idp.interfaces.dtos.doc import DocSpec
from flydesk_idp.interfaces.dtos.extract import (
    DocumentInfo,
    EscalationInfo,
    ExtractedDocument,
    ExtractionRequest,
    ExtractionResult,
)
from flydesk_idp.interfaces.dtos.field import ExtractedFieldGroup

logger = logging.getLogger(__name__)


# ===========================================================================
# Event handler -- structured logs for every node start / complete / fail.
# ===========================================================================


class _LoggingEventHandler:
    """Implements :class:`PipelineEventHandler`. Pure logging only."""

    def __init__(self, request_id: str) -> None:
        self._request_id = request_id

    async def on_node_start(self, node_id: str, pipeline_name: str) -> None:
        logger.info("[%s] node_start %s/%s", self._request_id, pipeline_name, node_id)

    async def on_node_complete(self, node_id: str, pipeline_name: str, latency_ms: float) -> None:
        logger.info(
            "[%s] node_done %s/%s latency_ms=%.0f",
            self._request_id,
            pipeline_name,
            node_id,
            latency_ms,
        )

    async def on_node_error(self, node_id: str, pipeline_name: str, error: str) -> None:
        logger.warning(
            "[%s] node_failed %s/%s error=%s", self._request_id, pipeline_name, node_id, error
        )

    async def on_node_skip(self, node_id: str, pipeline_name: str, reason: str) -> None:
        logger.info(
            "[%s] node_skipped %s/%s reason=%s",
            self._request_id,
            pipeline_name,
            node_id,
            reason,
        )

    async def on_pipeline_complete(
        self, pipeline_name: str, success: bool, duration_ms: float
    ) -> None:
        logger.info(
            "[%s] pipeline_complete %s success=%s duration_ms=%.0f",
            self._request_id,
            pipeline_name,
            success,
            duration_ms,
        )


# ===========================================================================
# Orchestrator
# ===========================================================================


class PipelineOrchestrator:
    """Builds + runs a :class:`PipelineEngine` for each extraction request."""

    PIPELINE_NAME = "flydesk-idp"

    def __init__(
        self,
        *,
        extractor: MultimodalExtractor,
        splitter: DocumentSplitter,
        field_validator: FieldValidator,
        visual_checker: VisualAuthenticityChecker,
        content_checker: ContentAuthenticityChecker,
        judge: Judge,
        rule_engine: RuleEngine,
        judge_escalator: JudgeEscalator,
        default_model: str,
    ) -> None:
        self._extractor = extractor
        self._splitter = splitter
        self._field_validator = field_validator
        self._visual_checker = visual_checker
        self._content_checker = content_checker
        self._judge = judge
        self._rule_engine = rule_engine
        self._judge_escalator = judge_escalator
        self._default_model = default_model

    async def execute(self, request: ExtractionRequest) -> ExtractionResult:
        started = time.monotonic()
        stages = request.options.stages
        model_id = request.options.model or self._default_model

        # Build the per-request pipeline. Stage toggles decide which nodes
        # are added so the resulting trace reflects exactly what ran.
        builder = PipelineBuilder(self.PIPELINE_NAME)
        chain: list[str] = []

        builder.add_node("load", CallableStep(self._step_load), timeout_seconds=10)
        chain.append("load")

        if stages.splitter and len(request.docs) > 1:
            builder.add_node("split", CallableStep(self._step_split), timeout_seconds=60)
            chain.append("split")

        builder.add_node("extract", CallableStep(self._step_extract), timeout_seconds=240)
        chain.append("extract")

        if stages.field_validation:
            builder.add_node(
                "field_validation",
                CallableStep(self._step_field_validation),
                timeout_seconds=5,
            )
            chain.append("field_validation")

        if stages.visual_authenticity:
            builder.add_node(
                "visual_authenticity",
                CallableStep(self._step_visual_authenticity),
                timeout_seconds=180,
            )
            chain.append("visual_authenticity")

        if stages.content_authenticity:
            builder.add_node(
                "content_authenticity",
                CallableStep(self._step_content_authenticity),
                timeout_seconds=180,
            )
            chain.append("content_authenticity")

        if stages.judge:
            builder.add_node("judge", CallableStep(self._step_judge), timeout_seconds=180)
            chain.append("judge")

        # judge_escalation requires judge to have produced the verdicts
        # we want to re-evaluate. Skip silently if judge is off.
        if stages.judge and stages.judge_escalation:
            builder.add_node(
                "judge_escalation",
                CallableStep(self._step_judge_escalation),
                timeout_seconds=300,
            )
            chain.append("judge_escalation")

        if stages.rule_engine and request.rules:
            builder.add_node("rules", CallableStep(self._step_rules), timeout_seconds=180)
            chain.append("rules")

        builder.add_node("assemble", CallableStep(self._step_assemble), timeout_seconds=5)
        chain.append("assemble")
        builder.chain(*chain)

        engine = builder.build()
        engine._event_handler = _LoggingEventHandler(str(request.request_id))  # noqa: SLF001

        ctx = PipelineContext(
            inputs=request,
            metadata={
                "request": request,
                "model_id": model_id,
                "pipeline_errors": [],
            },
            correlation_id=str(request.request_id),
        )

        await engine.run(context=ctx)

        latency_ms = int((time.monotonic() - started) * 1000)
        return self._build_result(request, ctx, model_id, latency_ms)

    # ------------------------------------------------------------------
    # Pipeline steps -- each reads/writes ``context.metadata``
    # ------------------------------------------------------------------

    async def _step_load(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> dict[str, Any]:
        request: ExtractionRequest = ctx.metadata["request"]
        document_bytes = request.document.decoded_bytes()
        loaded = load_document(
            document_bytes,
            declared_media_type=request.options.declared_media_type or request.document.content_type,
        )
        ctx.metadata["document_bytes"] = document_bytes
        ctx.metadata["loaded"] = loaded
        return {"media_type": loaded.media_type, "page_count": loaded.page_count}

    async def _step_split(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        loaded = ctx.metadata["loaded"]
        try:
            split = await self._splitter.split(
                document_bytes=ctx.metadata["document_bytes"],
                media_type=loaded.media_type,
                page_count=loaded.page_count,
                targets=request.docs,
                intention=request.intention,
                model=ctx.metadata["model_id"],
            )
            ctx.metadata["split_documents"] = split.documents
            ctx.metadata["additional_splits"] = split.additional_documents
            return {"split_documents": len(split.documents)}
        except Exception as exc:  # noqa: BLE001
            self._record_error(ctx, "splitter", "SPLITTER_ERROR", exc)
            return {"failed": True}

    async def _step_extract(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        loaded = ctx.metadata["loaded"]
        split_documents: list[SplitDocument] = ctx.metadata.get("split_documents") or [
            SplitDocument(
                document_type=d.docType.documentType,
                page_start=1,
                page_end=loaded.page_count,
                confidence=1.0,
                description=d.docType.description,
                missing=False,
            )
            for d in request.docs
        ]
        ctx.metadata["split_documents"] = split_documents

        per_doc_inputs: dict[str, tuple[bytes, str, int, DocSpec, SplitDocument]] = {}
        for doc_spec, split in zip(request.docs, split_documents, strict=True):
            doc_type = doc_spec.docType.documentType
            if split.missing:
                per_doc_inputs[doc_type] = (b"", loaded.media_type, 0, doc_spec, split)
                continue
            slice_bytes = ctx.metadata["document_bytes"]
            slice_pages = loaded.page_count
            if loaded.media_type == "application/pdf" and split.page_start is not None and (
                split.page_start != 1 or (split.page_end or loaded.page_count) != loaded.page_count
            ):
                try:
                    slice_bytes = slice_pdf(
                        ctx.metadata["document_bytes"],
                        PageRange(
                            start=split.page_start, end=split.page_end or loaded.page_count
                        ),
                    )
                    slice_pages = (split.page_end or loaded.page_count) - split.page_start + 1
                except Exception as exc:  # noqa: BLE001
                    self._record_error(
                        ctx, "pdf_slicer", "SLICE_ERROR", exc, doc_type=doc_type
                    )
            per_doc_inputs[doc_type] = (
                slice_bytes,
                loaded.media_type,
                slice_pages,
                doc_spec,
                split,
            )
        ctx.metadata["per_doc_inputs"] = per_doc_inputs

        per_doc_extracted: dict[str, list[ExtractedFieldGroup]] = {}
        per_doc_model_used: dict[str, str] = {}

        async def _extract_one(doc_type: str) -> None:
            slice_bytes, media_type, pages, doc_spec, split = per_doc_inputs[doc_type]
            if split.missing:
                per_doc_extracted[doc_type] = []
                return
            try:
                groups, used = await self._extractor.extract(
                    document_bytes=slice_bytes,
                    media_type=media_type,
                    page_count=pages,
                    doc=doc_spec,
                    intention=request.intention,
                    language_hint=request.options.language_hint,
                    model=ctx.metadata["model_id"],
                )
                per_doc_extracted[doc_type] = groups
                per_doc_model_used[doc_type] = used
            except Exception as exc:  # noqa: BLE001
                self._record_error(ctx, "extractor", "EXTRACTOR_ERROR", exc, doc_type=doc_type)
                per_doc_extracted[doc_type] = []

        await asyncio.gather(*(_extract_one(dt) for dt in per_doc_inputs))
        ctx.metadata["per_doc_extracted"] = per_doc_extracted
        ctx.metadata["per_doc_model_used"] = per_doc_model_used
        return {"docs_extracted": sum(1 for g in per_doc_extracted.values() if g)}

    async def _step_field_validation(
        self, ctx: PipelineContext, _inputs: dict[str, Any]
    ) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        per_doc_extracted: dict[str, list[ExtractedFieldGroup]] = ctx.metadata["per_doc_extracted"]
        for doc_spec in request.docs:
            doc_type = doc_spec.docType.documentType
            groups = per_doc_extracted.get(doc_type, [])
            self._field_validator.validate(doc_spec.fieldGroups, groups)
        return {"validated": True}

    async def _step_visual_authenticity(
        self, ctx: PipelineContext, _inputs: dict[str, Any]
    ) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        per_doc_inputs = ctx.metadata["per_doc_inputs"]
        per_doc_visual: dict[str, list[VisualValidationOutcome]] = {}

        async def _check_one(doc_type: str) -> None:
            slice_bytes, media_type, _, doc_spec, split = per_doc_inputs[doc_type]
            if split.missing or not doc_spec.validators.visual:
                per_doc_visual[doc_type] = []
                return
            try:
                outcomes = await self._visual_checker.check(
                    document_bytes=slice_bytes,
                    media_type=media_type,
                    doc=doc_spec,
                    intention=request.intention,
                    model=ctx.metadata["model_id"],
                )
                per_doc_visual[doc_type] = outcomes
            except Exception as exc:  # noqa: BLE001
                self._record_error(
                    ctx, "visual_authenticity", "VISUAL_AUTH_ERROR", exc, doc_type=doc_type
                )
                per_doc_visual[doc_type] = []

        await asyncio.gather(*(_check_one(dt) for dt in per_doc_inputs))
        ctx.metadata["per_doc_visual"] = per_doc_visual
        return {"docs_checked": len(per_doc_visual)}

    async def _step_content_authenticity(
        self, ctx: PipelineContext, _inputs: dict[str, Any]
    ) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        per_doc_inputs = ctx.metadata["per_doc_inputs"]
        per_doc_content: dict[str, ContentAuthenticity] = {}

        async def _audit_one(doc_type: str) -> None:
            slice_bytes, media_type, _, doc_spec, split = per_doc_inputs[doc_type]
            if split.missing:
                per_doc_content[doc_type] = ContentAuthenticity()
                return
            try:
                per_doc_content[doc_type] = await self._content_checker.check(
                    document_bytes=slice_bytes,
                    media_type=media_type,
                    doc=doc_spec,
                    intention=request.intention,
                    model=ctx.metadata["model_id"],
                )
            except Exception as exc:  # noqa: BLE001
                self._record_error(
                    ctx, "content_authenticity", "CONTENT_AUTH_ERROR", exc, doc_type=doc_type
                )
                per_doc_content[doc_type] = ContentAuthenticity()

        await asyncio.gather(*(_audit_one(dt) for dt in per_doc_inputs))
        ctx.metadata["per_doc_content"] = per_doc_content
        return {"docs_audited": len(per_doc_content)}

    async def _step_judge(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        per_doc_inputs = ctx.metadata["per_doc_inputs"]
        per_doc_extracted = ctx.metadata["per_doc_extracted"]

        async def _judge_one(doc_type: str) -> None:
            slice_bytes, media_type, _, doc_spec, split = per_doc_inputs[doc_type]
            groups = per_doc_extracted.get(doc_type, [])
            if split.missing or not groups:
                return
            try:
                await self._judge.judge(
                    document_bytes=slice_bytes,
                    media_type=media_type,
                    doc=doc_spec,
                    extracted_groups=groups,
                    intention=request.intention,
                    model=ctx.metadata["model_id"],
                )
            except Exception as exc:  # noqa: BLE001
                self._record_error(ctx, "judge", "JUDGE_ERROR", exc, doc_type=doc_type)

        await asyncio.gather(*(_judge_one(dt) for dt in per_doc_inputs))
        return {"judged": True}

    async def _step_judge_escalation(
        self, ctx: PipelineContext, _inputs: dict[str, Any]
    ) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        try:
            info = await self._judge_escalator.maybe_escalate(ctx, request)
            if info is not None:
                ctx.metadata["escalation"] = info
                return {"escalation_triggered": True, "accepted": info.accepted}
            return {"escalation_triggered": False}
        except Exception as exc:  # noqa: BLE001
            self._record_error(ctx, "judge_escalation", "ESCALATION_ERROR", exc)
            return {"failed": True}

    async def _step_rules(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        per_doc_extracted = ctx.metadata["per_doc_extracted"]
        per_doc_visual = ctx.metadata.get("per_doc_visual", {})
        try:
            rule_results = await self._rule_engine.evaluate(
                request.rules,
                docs=request.docs,
                extracted_by_doc=per_doc_extracted,
                visual_by_doc=per_doc_visual,
                intention=request.intention,
                model=ctx.metadata["model_id"],
            )
            ctx.metadata["rule_results"] = rule_results
            return {"rules_evaluated": len(rule_results)}
        except Exception as exc:  # noqa: BLE001
            self._record_error(ctx, "rule_engine", "RULE_ENGINE_ERROR", exc)
            ctx.metadata["rule_results"] = []
            return {"failed": True}

    async def _step_assemble(self, _ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        # Pure marker -- result composition happens in :meth:`_build_result`.
        return {"assembled": True}

    # ------------------------------------------------------------------

    def _record_error(
        self,
        ctx: PipelineContext,
        node: str,
        code: str,
        exc: Exception,
        *,
        doc_type: str | None = None,
    ) -> None:
        message = f"{doc_type}: {exc}" if doc_type else str(exc)
        logger.error("Pipeline node %s failed: %s", node, message)
        ctx.metadata["pipeline_errors"].append({"node": node, "code": code, "message": message})

    def _build_result(
        self,
        request: ExtractionRequest,
        ctx: PipelineContext,
        model_id: str,
        latency_ms: int,
    ) -> ExtractionResult:
        loaded = ctx.metadata["loaded"]
        split_documents: list[SplitDocument] = ctx.metadata.get("split_documents") or []
        per_doc_extracted: dict[str, list[ExtractedFieldGroup]] = ctx.metadata.get(
            "per_doc_extracted", {}
        )
        per_doc_visual: dict[str, list[VisualValidationOutcome]] = ctx.metadata.get(
            "per_doc_visual", {}
        )
        per_doc_content: dict[str, ContentAuthenticity] = ctx.metadata.get("per_doc_content", {})
        per_doc_model_used: dict[str, str] = ctx.metadata.get("per_doc_model_used", {})
        rule_results = ctx.metadata.get("rule_results", [])

        used_models = set(per_doc_model_used.values())
        if used_models:
            model_id = (
                ",".join(sorted(used_models)) if len(used_models) > 1 else next(iter(used_models))
            )

        documents: list[ExtractedDocument] = []
        split_by_type: dict[str, SplitDocument] = {s.document_type: s for s in split_documents}
        for doc_spec in request.docs:
            doc_type = doc_spec.docType.documentType
            split = split_by_type.get(doc_type) or SplitDocument(
                document_type=doc_type, missing=True
            )
            documents.append(
                ExtractedDocument(
                    document_type=doc_type,
                    missing=split.missing,
                    pages=_pages_range(split.page_start, split.page_end),
                    description=split.description or doc_spec.docType.description,
                    confidence=split.confidence,
                    fields=per_doc_extracted.get(doc_type, []),
                    authenticity=DocumentAuthenticity(
                        visual=per_doc_visual.get(doc_type, []),
                        content=per_doc_content.get(doc_type, ContentAuthenticity()),
                    ),
                )
            )

        additional_documents = [
            ExtractedDocument(
                document_type=s.document_type,
                missing=False,
                pages=_pages_range(s.page_start, s.page_end),
                description=s.description,
                confidence=s.confidence,
            )
            for s in ctx.metadata.get("additional_splits", [])
        ]

        escalation: EscalationInfo | None = ctx.metadata.get("escalation")
        return ExtractionResult(
            request_id=request.request_id,
            document=DocumentInfo(
                filename=request.document.filename,
                media_type=loaded.media_type,
                page_count=loaded.page_count,
                bytes=loaded.size_bytes,
            ),
            documents=documents,
            additional_documents=additional_documents,
            rule_results=rule_results,
            model=model_id,
            latency_ms=latency_ms,
            pipeline_errors=ctx.metadata.get("pipeline_errors", []),
            escalation=escalation,
        )


def _pages_range(start: int | None, end: int | None) -> list[int]:
    if start is None or end is None or end < start:
        return []
    return list(range(start, end + 1))
