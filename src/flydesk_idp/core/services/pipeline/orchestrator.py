# Copyright 2026 Firefly Software Solutions Inc
"""``PipelineOrchestrator`` -- runs the IDP pipeline as a
:class:`fireflyframework_agentic.pipeline.PipelineEngine` DAG.

Two input shapes converge onto the same downstream pipeline:

* **single file, single or many DocSpecs** -- the legacy shape. One
  load + an optional splitter + one extraction per DocSpec.
* **multi-file** -- ``request.documents = [...]``. Each file is loaded
  independently and (when no caller pin) classified into one of the
  declared DocSpecs. One extraction per (file, DocSpec) pair.

Internally the orchestrator normalises both shapes into a flat
``tasks`` list. Every downstream stage (extract, validators,
authenticity, judge, rules) iterates that list, so the per-stage code
doesn't care whether the source was one file or many.

The method is called :py:meth:`execute` rather than ``run`` so it does
**not** accidentally satisfy pyfly's ``CommandLineRunner`` structural
protocol (which would auto-invoke ``run(sys.argv[1:])`` at startup).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
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
from flydesk_idp.core.services.bbox import BboxValidator
from flydesk_idp.core.services.classification import (
    UNMATCHED,
    ClassificationResult,
    DocumentClassifier,
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
    ClassificationInfo,
    DocumentInfo,
    DocumentInput,
    EscalationInfo,
    ExtractedDocument,
    ExtractionRequest,
    ExtractionResult,
)
from flydesk_idp.interfaces.dtos.field import ExtractedFieldGroup

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _FileSlot:
    """Per-input-file state populated during ``_step_load``."""

    file_index: int
    filename: str
    media_type: str
    page_count: int
    document_bytes: bytes
    declared_doctype: str | None    # pinned by the caller, may be None
    resolved_doctype: str | None = None    # after classifier; mirrors declared when pinned
    classification: ClassificationResult | None = None    # null when pinned


@dataclass(slots=True)
class _ExtractionTask:
    """One (file, DocSpec) pair the downstream stages will iterate over."""

    task_id: str                        # ``f"file{i}/{doc_type}"`` -- unique
    file_index: int
    filename: str
    media_type: str
    doc_spec: DocSpec
    split: SplitDocument
    slice_bytes: bytes
    slice_pages: int
    extracted_groups: list[ExtractedFieldGroup] = field(default_factory=list)
    model_used: str | None = None
    visual: list[VisualValidationOutcome] = field(default_factory=list)
    content: ContentAuthenticity = field(default_factory=ContentAuthenticity)


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
            self._request_id, pipeline_name, node_id, latency_ms,
        )

    async def on_node_error(self, node_id: str, pipeline_name: str, error: str) -> None:
        logger.warning(
            "[%s] node_failed %s/%s error=%s", self._request_id, pipeline_name, node_id, error
        )

    async def on_node_skip(self, node_id: str, pipeline_name: str, reason: str) -> None:
        logger.info(
            "[%s] node_skipped %s/%s reason=%s", self._request_id, pipeline_name, node_id, reason
        )

    async def on_pipeline_complete(
        self, pipeline_name: str, success: bool, duration_ms: float
    ) -> None:
        logger.info(
            "[%s] pipeline_complete %s success=%s duration_ms=%.0f",
            self._request_id, pipeline_name, success, duration_ms,
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
        classifier: DocumentClassifier,
        field_validator: FieldValidator,
        bbox_validator: BboxValidator,
        visual_checker: VisualAuthenticityChecker,
        content_checker: ContentAuthenticityChecker,
        judge: Judge,
        rule_engine: RuleEngine,
        judge_escalator: JudgeEscalator,
        default_model: str,
    ) -> None:
        self._extractor = extractor
        self._splitter = splitter
        self._classifier = classifier
        self._field_validator = field_validator
        self._bbox_validator = bbox_validator
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
        files = request.files
        is_multi_file = len(files) > 1 or any(f.document_type for f in files)

        builder = PipelineBuilder(self.PIPELINE_NAME)
        chain: list[str] = []

        builder.add_node("load", CallableStep(self._step_load), timeout_seconds=20)
        chain.append("load")

        # Classifier runs only when at least one file lacks a pinned doctype
        # AND the caller hasn't disabled it. In single-file mode with a
        # single docSpec, classification is irrelevant.
        needs_classifier = (
            stages.classifier and is_multi_file and any(not f.document_type for f in files)
        )
        if needs_classifier:
            builder.add_node(
                "classifier", CallableStep(self._step_classifier), timeout_seconds=120
            )
            chain.append("classifier")

        # Splitter is single-file only -- in multi-file mode each file
        # is already one document.
        if stages.splitter and not is_multi_file and len(request.docs) > 1:
            builder.add_node("split", CallableStep(self._step_split), timeout_seconds=60)
            chain.append("split")

        builder.add_node("plan_tasks", CallableStep(self._step_plan_tasks), timeout_seconds=5)
        chain.append("plan_tasks")

        builder.add_node("extract", CallableStep(self._step_extract), timeout_seconds=300)
        chain.append("extract")

        builder.add_node(
            "bbox_validation", CallableStep(self._step_bbox_validation), timeout_seconds=5
        )
        chain.append("bbox_validation")

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
                "is_multi_file": is_multi_file,
                "pipeline_errors": [],
                "additional_files": [],   # files the classifier marked as unmatched
            },
            correlation_id=str(request.request_id),
        )

        await engine.run(context=ctx)

        latency_ms = int((time.monotonic() - started) * 1000)
        return self._build_result(request, ctx, model_id, latency_ms)

    # ------------------------------------------------------------------
    # Pipeline steps
    # ------------------------------------------------------------------

    async def _step_load(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        files: list[_FileSlot] = []
        for i, file in enumerate(request.files):
            document_bytes = file.decoded_bytes()
            loaded = load_document(
                document_bytes,
                declared_media_type=request.options.declared_media_type or file.content_type,
            )
            files.append(_FileSlot(
                file_index=i,
                filename=file.filename,
                media_type=loaded.media_type,
                page_count=loaded.page_count,
                document_bytes=document_bytes,
                declared_doctype=file.document_type,
                resolved_doctype=file.document_type,    # mirrors declared until classifier runs
            ))
        ctx.metadata["files_data"] = files
        return {"file_count": len(files), "is_multi_file": ctx.metadata["is_multi_file"]}

    async def _step_classifier(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        files: list[_FileSlot] = ctx.metadata["files_data"]
        unclassified = [slot for slot in files if not slot.declared_doctype]
        if not unclassified:
            return {"classified": 0}

        async def _classify_one(slot: _FileSlot) -> None:
            try:
                result = await self._classifier.classify(
                    document_bytes=slot.document_bytes,
                    media_type=slot.media_type,
                    filename=slot.filename,
                    candidates=request.docs,
                    intention=request.intention,
                    model=ctx.metadata["model_id"],
                )
                slot.classification = result
                slot.resolved_doctype = (
                    result.document_type if result.matched else None
                )
            except Exception as exc:  # noqa: BLE001
                self._record_error(ctx, "classifier", "CLASSIFIER_ERROR", exc, doc_type=slot.filename)
                slot.classification = ClassificationResult(
                    document_type=UNMATCHED, matched=False, notes=str(exc)[:200]
                )
                slot.resolved_doctype = None

        await asyncio.gather(*(_classify_one(s) for s in unclassified))
        matched = sum(1 for s in unclassified if s.resolved_doctype)
        return {"classified": matched, "unmatched": len(unclassified) - matched}

    async def _step_split(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        files: list[_FileSlot] = ctx.metadata["files_data"]
        assert len(files) == 1, "splitter only runs in single-file mode"
        only = files[0]
        try:
            split = await self._splitter.split(
                document_bytes=only.document_bytes,
                media_type=only.media_type,
                page_count=only.page_count,
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

    async def _step_plan_tasks(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        """Build the flat list of (file, DocSpec) tasks the rest of the pipeline iterates over."""
        request: ExtractionRequest = ctx.metadata["request"]
        files: list[_FileSlot] = ctx.metadata["files_data"]
        is_multi_file: bool = ctx.metadata["is_multi_file"]
        doc_spec_by_type = {d.docType.documentType: d for d in request.docs}
        tasks: list[_ExtractionTask] = []
        additional_files: list[dict[str, Any]] = list(ctx.metadata.get("additional_files", []))

        if is_multi_file:
            # One task per file (assigned to its resolved DocSpec).
            for slot in files:
                if not slot.resolved_doctype:
                    additional_files.append({
                        "file_index": slot.file_index,
                        "filename": slot.filename,
                        "reason": "classifier_unmatched",
                        "notes": slot.classification.notes if slot.classification else "",
                    })
                    continue
                doc_spec = doc_spec_by_type.get(slot.resolved_doctype)
                if doc_spec is None:
                    additional_files.append({
                        "file_index": slot.file_index,
                        "filename": slot.filename,
                        "reason": "doctype_not_declared",
                        "resolved_doctype": slot.resolved_doctype,
                    })
                    continue
                tasks.append(_ExtractionTask(
                    task_id=f"file{slot.file_index}/{slot.resolved_doctype}",
                    file_index=slot.file_index,
                    filename=slot.filename,
                    media_type=slot.media_type,
                    doc_spec=doc_spec,
                    split=SplitDocument(
                        document_type=slot.resolved_doctype,
                        page_start=1,
                        page_end=slot.page_count,
                        confidence=(
                            slot.classification.confidence
                            if slot.classification else 1.0
                        ),
                        description=(
                            slot.classification.description
                            if slot.classification else ""
                        ),
                        missing=False,
                    ),
                    slice_bytes=slot.document_bytes,
                    slice_pages=slot.page_count,
                ))
        else:
            # Single-file legacy: one task per DocSpec, page-ranged by the splitter
            # (if it ran) or full-range by default.
            slot = files[0]
            split_documents: list[SplitDocument] = ctx.metadata.get("split_documents") or [
                SplitDocument(
                    document_type=d.docType.documentType,
                    page_start=1,
                    page_end=slot.page_count,
                    confidence=1.0,
                    description=d.docType.description,
                    missing=False,
                )
                for d in request.docs
            ]
            ctx.metadata["split_documents"] = split_documents
            for doc_spec, split in zip(request.docs, split_documents, strict=True):
                doc_type = doc_spec.docType.documentType
                if split.missing:
                    tasks.append(_ExtractionTask(
                        task_id=f"file{slot.file_index}/{doc_type}",
                        file_index=slot.file_index,
                        filename=slot.filename,
                        media_type=slot.media_type,
                        doc_spec=doc_spec,
                        split=split,
                        slice_bytes=b"",
                        slice_pages=0,
                    ))
                    continue
                slice_bytes = slot.document_bytes
                slice_pages = slot.page_count
                if slot.media_type == "application/pdf" and (
                    split.page_start != 1 or (split.page_end or slot.page_count) != slot.page_count
                ):
                    try:
                        slice_bytes = slice_pdf(
                            slot.document_bytes,
                            PageRange(start=split.page_start, end=split.page_end or slot.page_count),
                        )
                        slice_pages = (split.page_end or slot.page_count) - split.page_start + 1
                    except Exception as exc:  # noqa: BLE001
                        self._record_error(ctx, "pdf_slicer", "SLICE_ERROR", exc, doc_type=doc_type)
                tasks.append(_ExtractionTask(
                    task_id=f"file{slot.file_index}/{doc_type}",
                    file_index=slot.file_index,
                    filename=slot.filename,
                    media_type=slot.media_type,
                    doc_spec=doc_spec,
                    split=split,
                    slice_bytes=slice_bytes,
                    slice_pages=slice_pages,
                ))

        ctx.metadata["tasks"] = tasks
        ctx.metadata["additional_files"] = additional_files
        return {"task_count": len(tasks), "additional": len(additional_files)}

    async def _step_extract(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        tasks: list[_ExtractionTask] = ctx.metadata["tasks"]

        async def _extract_one(task: _ExtractionTask) -> None:
            if task.split.missing or not task.slice_bytes:
                return
            try:
                groups, used = await self._extractor.extract(
                    document_bytes=task.slice_bytes,
                    media_type=task.media_type,
                    page_count=task.slice_pages,
                    doc=task.doc_spec,
                    intention=request.intention,
                    language_hint=request.options.language_hint,
                    model=ctx.metadata["model_id"],
                )
                task.extracted_groups = groups
                task.model_used = used
            except Exception as exc:  # noqa: BLE001
                self._record_error(
                    ctx, "extractor", "EXTRACTOR_ERROR", exc, doc_type=task.task_id
                )

        await asyncio.gather(*(_extract_one(t) for t in tasks))
        return {"docs_extracted": sum(1 for t in tasks if t.extracted_groups)}

    async def _step_bbox_validation(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        tasks: list[_ExtractionTask] = ctx.metadata["tasks"]
        for task in tasks:
            if task.extracted_groups:
                self._bbox_validator.validate_groups(task.extracted_groups)
        return {"validated": True}

    async def _step_field_validation(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        tasks: list[_ExtractionTask] = ctx.metadata["tasks"]
        for task in tasks:
            self._field_validator.validate(task.doc_spec.fieldGroups, task.extracted_groups)
        return {"validated": True}

    async def _step_visual_authenticity(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        tasks: list[_ExtractionTask] = ctx.metadata["tasks"]

        async def _check_one(task: _ExtractionTask) -> None:
            if task.split.missing or not task.doc_spec.validators.visual or not task.slice_bytes:
                return
            try:
                outcomes = await self._visual_checker.check(
                    document_bytes=task.slice_bytes,
                    media_type=task.media_type,
                    doc=task.doc_spec,
                    intention=request.intention,
                    model=ctx.metadata["model_id"],
                )
                task.visual = outcomes
            except Exception as exc:  # noqa: BLE001
                self._record_error(
                    ctx, "visual_authenticity", "VISUAL_AUTH_ERROR", exc, doc_type=task.task_id
                )

        await asyncio.gather(*(_check_one(t) for t in tasks))
        return {"docs_checked": len(tasks)}

    async def _step_content_authenticity(
        self, ctx: PipelineContext, _inputs: dict[str, Any]
    ) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        tasks: list[_ExtractionTask] = ctx.metadata["tasks"]

        async def _audit_one(task: _ExtractionTask) -> None:
            if task.split.missing or not task.slice_bytes:
                return
            try:
                task.content = await self._content_checker.check(
                    document_bytes=task.slice_bytes,
                    media_type=task.media_type,
                    doc=task.doc_spec,
                    intention=request.intention,
                    model=ctx.metadata["model_id"],
                )
            except Exception as exc:  # noqa: BLE001
                self._record_error(
                    ctx, "content_authenticity", "CONTENT_AUTH_ERROR", exc, doc_type=task.task_id
                )

        await asyncio.gather(*(_audit_one(t) for t in tasks))
        return {"docs_audited": len(tasks)}

    async def _step_judge(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        tasks: list[_ExtractionTask] = ctx.metadata["tasks"]

        async def _judge_one(task: _ExtractionTask) -> None:
            if task.split.missing or not task.extracted_groups or not task.slice_bytes:
                return
            try:
                await self._judge.judge(
                    document_bytes=task.slice_bytes,
                    media_type=task.media_type,
                    doc=task.doc_spec,
                    extracted_groups=task.extracted_groups,
                    intention=request.intention,
                    model=ctx.metadata["model_id"],
                )
            except Exception as exc:  # noqa: BLE001
                self._record_error(ctx, "judge", "JUDGE_ERROR", exc, doc_type=task.task_id)

        await asyncio.gather(*(_judge_one(t) for t in tasks))
        return {"judged": True}

    async def _step_judge_escalation(
        self, ctx: PipelineContext, _inputs: dict[str, Any]
    ) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        # Re-shape the task-based state into the per_doc_* maps the escalator
        # was built against -- it stays orchestrator-agnostic that way.
        tasks: list[_ExtractionTask] = ctx.metadata["tasks"]
        per_doc_extracted = {t.task_id: t.extracted_groups for t in tasks}
        per_doc_inputs = {
            t.task_id: (t.slice_bytes, t.media_type, t.slice_pages, t.doc_spec, t.split)
            for t in tasks
        }
        per_doc_model_used = {t.task_id: t.model_used or ctx.metadata["model_id"] for t in tasks}
        ctx.metadata["per_doc_extracted"] = per_doc_extracted
        ctx.metadata["per_doc_inputs"] = per_doc_inputs
        ctx.metadata["per_doc_model_used"] = per_doc_model_used
        try:
            info = await self._judge_escalator.maybe_escalate(ctx, request)
            if info is not None:
                ctx.metadata["escalation"] = info
                # Mirror the (possibly replaced) extractions back onto tasks.
                if info.accepted:
                    for t in tasks:
                        t.extracted_groups = ctx.metadata["per_doc_extracted"][t.task_id]
                        t.model_used = ctx.metadata["per_doc_model_used"].get(t.task_id, t.model_used)
                    # Re-run bbox validation on the replaced extractions.
                    for t in tasks:
                        if t.extracted_groups:
                            self._bbox_validator.validate_groups(t.extracted_groups)
                return {"escalation_triggered": True, "accepted": info.accepted}
            return {"escalation_triggered": False}
        except Exception as exc:  # noqa: BLE001
            self._record_error(ctx, "judge_escalation", "ESCALATION_ERROR", exc)
            return {"failed": True}

    async def _step_rules(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        tasks: list[_ExtractionTask] = ctx.metadata["tasks"]

        # The rule engine takes per-doctype maps. Group by doctype across files.
        extracted_by_doc: dict[str, list[ExtractedFieldGroup]] = {}
        visual_by_doc: dict[str, list[VisualValidationOutcome]] = {}
        for t in tasks:
            doc_type = t.doc_spec.docType.documentType
            extracted_by_doc.setdefault(doc_type, []).extend(t.extracted_groups)
            visual_by_doc.setdefault(doc_type, []).extend(t.visual)
        try:
            rule_results = await self._rule_engine.evaluate(
                request.rules,
                docs=request.docs,
                extracted_by_doc=extracted_by_doc,
                visual_by_doc=visual_by_doc,
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
        files_data: list[_FileSlot] = ctx.metadata.get("files_data", [])
        tasks: list[_ExtractionTask] = ctx.metadata.get("tasks", [])
        rule_results = ctx.metadata.get("rule_results", [])
        is_multi_file: bool = ctx.metadata.get("is_multi_file", False)
        additional_splits = ctx.metadata.get("additional_splits", [])

        files_info = [
            DocumentInfo(
                filename=slot.filename,
                media_type=slot.media_type,
                page_count=slot.page_count,
                bytes=len(slot.document_bytes),
                document_type=slot.resolved_doctype,
                classification=_classification_info(slot.classification),
            )
            for slot in files_data
        ]
        # Legacy ``document`` field for single-file requests.
        document_info = files_info[0] if (files_info and not is_multi_file) else None

        # Resolve the model field by aggregating what the extractor actually
        # used across tasks (handles fallback + escalation).
        used_models = {t.model_used for t in tasks if t.model_used}
        if used_models:
            model_id = (
                ",".join(sorted(used_models)) if len(used_models) > 1 else next(iter(used_models))
            )

        documents: list[ExtractedDocument] = []
        for task in tasks:
            documents.append(ExtractedDocument(
                document_type=task.doc_spec.docType.documentType,
                missing=task.split.missing,
                pages=_pages_range(task.split.page_start, task.split.page_end),
                description=task.split.description or task.doc_spec.docType.description,
                confidence=task.split.confidence,
                fields=task.extracted_groups,
                authenticity=DocumentAuthenticity(visual=task.visual, content=task.content),
                source_file=task.filename if is_multi_file else None,
            ))

        # additional_documents: splitter leftovers + classifier-unmatched files.
        additional_documents = [
            ExtractedDocument(
                document_type=s.document_type,
                missing=False,
                pages=_pages_range(s.page_start, s.page_end),
                description=s.description,
                confidence=s.confidence,
            )
            for s in additional_splits
        ]
        for entry in ctx.metadata.get("additional_files", []):
            additional_documents.append(ExtractedDocument(
                document_type="unmatched",
                missing=True,
                description=entry.get("notes") or entry.get("reason", ""),
                confidence=0.0,
                source_file=entry.get("filename"),
            ))

        escalation: EscalationInfo | None = ctx.metadata.get("escalation")
        return ExtractionResult(
            request_id=request.request_id,
            document=document_info,
            files=files_info,
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


def _classification_info(result: ClassificationResult | None) -> ClassificationInfo | None:
    if result is None:
        return None
    return ClassificationInfo(
        document_type=result.document_type,
        matched=result.matched,
        confidence=result.confidence,
        description=result.description,
        notes=result.notes,
    )
