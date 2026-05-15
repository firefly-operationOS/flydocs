# Copyright 2026 Firefly Software Solutions Inc
"""``PipelineOrchestrator`` -- runs the IDP pipeline as a
:class:`fireflyframework_agentic.pipeline.PipelineEngine` DAG.

Every input file (whether the caller submitted ``document`` or
``documents``) flows through the same stages:

    load -> discover? -> classify? -> plan_tasks -> extract ->
    bbox_validation -> field_validation? -> visual? -> content? ->
    judge? -> judge_escalation? -> rules? -> assemble

The discover stage (``stages.splitter``) enumerates every distinct
sub-document inside a file, so a single uploaded PDF that happens to
contain a deed + a DNI + a utility bill comes out as three segments
rather than one. The classifier then runs **per segment** and assigns
each one to a declared ``DocSpec`` (or ``unmatched``). One extraction
task is produced per matched (segment, DocSpec) pair.

Skip rules:

* Files pinned with ``document_type`` skip the splitter and the
  classifier -- the caller already told us what that file is.
* Single-page files skip the splitter (one segment is enough).
* Segments that already have a resolved doctype (pinned, or only one
  declared DocSpec is on offer) skip the classifier.

The method is called ``execute`` rather than ``run`` so it does **not**
accidentally satisfy pyfly's ``CommandLineRunner`` structural protocol
(which would auto-invoke ``run(sys.argv[1:])`` at startup).
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

from flydesk_idp.config import IDPSettings
from flydesk_idp.core.observability import (
    reset_correlation_id,
    set_correlation_id,
)
from flydesk_idp.core.services.authenticity import (
    ContentAuthenticityChecker,
    VisualAuthenticityChecker,
)
from flydesk_idp.core.services.bbox import BboxRefiner, BboxValidator
from flydesk_idp.core.services.binary import BinaryNormalizer
from flydesk_idp.core.services.classification import (
    UNMATCHED,
    ClassificationResult,
    DocumentClassifier,
)
from flydesk_idp.core.services.escalation import JudgeEscalator
from flydesk_idp.core.services.extraction.extractor import MultimodalExtractor
from flydesk_idp.core.services.extraction.pdf_slicer import PageRange, slice_pdf
from flydesk_idp.core.services.judge import Judge
from flydesk_idp.core.services.rules import RuleEngine
from flydesk_idp.core.services.splitting import DiscoveredSegment, DocumentSplitter
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
    EscalationInfo,
    ExtractedDocument,
    ExtractionRequest,
    ExtractionResult,
    TraceEntry,
    UsageBreakdown,
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
    declared_doctype: str | None  # pinned by the caller, may be None
    segments: list[_Segment] = field(default_factory=list)


@dataclass(slots=True)
class _Segment:
    """One sub-document inside a file (discovered or implicit)."""

    file_index: int
    filename: str
    media_type: str
    page_start: int  # 1-indexed, inclusive
    page_end: int  # 1-indexed, inclusive
    file_page_count: int  # pages in the parent file
    provisional_type: str = ""  # splitter's free-text hint
    description: str = ""  # splitter or classifier description
    segmentation_confidence: float = 1.0
    # Filled by pin or by the classifier:
    resolved_doctype: str | None = None
    classification: ClassificationResult | None = None
    pinned: bool = False  # True when resolved_doctype came from a caller pin


@dataclass(slots=True)
class _ExtractionTask:
    """One (segment, DocSpec) pair the downstream stages iterate over."""

    task_id: str  # unique: ``f"file{i}/seg{j}/{doc_type}"``
    segment: _Segment
    doc_spec: DocSpec
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
        logger.info("Pipeline node started [%s] request_id=%s", node_id, self._request_id)

    async def on_node_complete(self, node_id: str, pipeline_name: str, latency_ms: float) -> None:
        logger.info(
            "Pipeline node complete [%s] latency_ms=%.0f request_id=%s",
            node_id,
            latency_ms,
            self._request_id,
        )

    async def on_node_error(self, node_id: str, pipeline_name: str, error: str) -> None:
        logger.error(
            "Pipeline node failed [%s] error=%s request_id=%s",
            node_id,
            error,
            self._request_id,
        )

    async def on_node_skip(self, node_id: str, pipeline_name: str, reason: str) -> None:
        logger.info(
            "Pipeline node skipped [%s] reason=%s request_id=%s",
            node_id,
            reason,
            self._request_id,
        )

    async def on_pipeline_complete(self, pipeline_name: str, success: bool, duration_ms: float) -> None:
        logger.info(
            "Pipeline complete name=%s success=%s duration_ms=%.0f request_id=%s",
            pipeline_name,
            success,
            duration_ms,
            self._request_id,
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
        bbox_refiner: BboxRefiner,
        binary_normalizer: BinaryNormalizer,
        visual_checker: VisualAuthenticityChecker,
        content_checker: ContentAuthenticityChecker,
        judge: Judge,
        rule_engine: RuleEngine,
        judge_escalator: JudgeEscalator,
        settings: IDPSettings,
        default_model: str,
    ) -> None:
        self._extractor = extractor
        self._splitter = splitter
        self._classifier = classifier
        self._field_validator = field_validator
        self._bbox_validator = bbox_validator
        self._bbox_refiner = bbox_refiner
        self._binary_normalizer = binary_normalizer
        self._visual_checker = visual_checker
        self._content_checker = content_checker
        self._judge = judge
        self._rule_engine = rule_engine
        self._judge_escalator = judge_escalator
        self._settings = settings
        self._default_model = default_model

    async def execute(self, request: ExtractionRequest) -> ExtractionResult:
        started = time.monotonic()
        # Bind the request id to the active asyncio context so every
        # downstream ``timed_agent_run`` tags its UsageRecord with this
        # correlation id. The reset happens in ``finally`` further down
        # so the var is always cleared even when the pipeline raises.
        correlation_token = set_correlation_id(str(request.request_id))
        try:
            return await self._execute_inner(request, started)
        finally:
            reset_correlation_id(correlation_token)

    async def _execute_inner(self, request: ExtractionRequest, started: float) -> ExtractionResult:
        stages = request.options.stages
        model_id = request.options.model or self._default_model
        files = request.files
        is_multi_file = len(files) > 1 or any(f.document_type for f in files)

        builder = PipelineBuilder(self.PIPELINE_NAME)
        chain: list[str] = []

        builder.add_node("load", CallableStep(self._step_load), timeout_seconds=20)
        chain.append("load")

        # Discover runs when the splitter is on AND at least one file is
        # unpinned (pinned files skip discovery — the caller already said
        # what they are) AND that file has more than one page.
        needs_discover = stages.splitter and any((not f.document_type) for f in files)
        if needs_discover:
            builder.add_node(
                "discover",
                CallableStep(self._step_discover),
                timeout_seconds=self._settings.splitter_timeout_s,
            )
            chain.append("discover")

        # Classifier runs per-segment when on. The step itself short-circuits
        # when there are no segments needing a doctype.
        if stages.classifier:
            builder.add_node(
                "classify",
                CallableStep(self._step_classifier),
                timeout_seconds=self._settings.classifier_timeout_s,
            )
            chain.append("classify")

        builder.add_node("plan_tasks", CallableStep(self._step_plan_tasks), timeout_seconds=5)
        chain.append("plan_tasks")

        builder.add_node(
            "extract",
            CallableStep(self._step_extract),
            timeout_seconds=self._settings.extract_timeout_s,
        )
        chain.append("extract")

        builder.add_node("bbox_validation", CallableStep(self._step_bbox_validation), timeout_seconds=5)
        chain.append("bbox_validation")

        if stages.bbox_refine:
            builder.add_node(
                "bbox_refine",
                CallableStep(self._step_bbox_refine),
                timeout_seconds=self._settings.bbox_refine_inline_timeout_s,
            )
            chain.append("bbox_refine")

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
            builder.add_node(
                "judge",
                CallableStep(self._step_judge),
                timeout_seconds=self._settings.judge_timeout_s,
            )
            chain.append("judge")

        if stages.judge and stages.judge_escalation:
            builder.add_node(
                "judge_escalation",
                CallableStep(self._step_judge_escalation),
                timeout_seconds=self._settings.judge_escalation_timeout_s,
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
                "unmatched_segments": [],  # segments the classifier left without a docType
            },
            correlation_id=str(request.request_id),
        )

        pipeline_result = await engine.run(context=ctx)

        latency_ms = int((time.monotonic() - started) * 1000)
        return self._build_result(request, ctx, model_id, latency_ms, pipeline_result=pipeline_result)

    # ------------------------------------------------------------------
    # Pipeline steps
    # ------------------------------------------------------------------

    async def _step_load(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        """Normalise every input binary and emit one ``_FileSlot`` per row.

        Each inbound :class:`DocumentInput` flows through
        :class:`BinaryNormalizer`. A born-digital PDF or a clean PNG
        passes through 1:1; a DOCX is converted to PDF; a HEIC photo
        becomes PNG; a multi-frame TIFF becomes a multi-page PDF; a ZIP
        / EML+attachments fan out into multiple slots. ``derived_from``
        chains let the response trace each output back to the original
        upload.

        The doctype pin survives only when normalisation produced
        exactly one row -- a multi-row expansion (ZIP, email) makes the
        original pin ambiguous, so the classifier is asked to decide
        per-row.
        """
        request: ExtractionRequest = ctx.metadata["request"]
        files: list[_FileSlot] = []
        # Slot index is monotonic across the expansion of all inputs.
        slot_index = 0
        for file in request.files:
            document_bytes = file.decoded_bytes()
            normalised = await self._binary_normalizer.normalise(
                document_bytes,
                declared_media_type=request.options.declared_media_type or file.content_type,
                filename=file.filename,
            )
            multi_row = len(normalised) > 1
            for row in normalised:
                effective_doctype = file.document_type if not multi_row else None
                slot_filename = (
                    "/".join((*row.derived_from, row.filename)) if row.derived_from else row.filename
                )
                slot = _FileSlot(
                    file_index=slot_index,
                    filename=slot_filename,
                    media_type=row.media_type,
                    page_count=row.page_count,
                    document_bytes=row.bytes,
                    declared_doctype=effective_doctype,
                )
                slot.segments = [
                    _Segment(
                        file_index=slot_index,
                        filename=slot.filename,
                        media_type=slot.media_type,
                        page_start=1,
                        page_end=slot.page_count,
                        file_page_count=slot.page_count,
                        segmentation_confidence=1.0,
                        resolved_doctype=slot.declared_doctype,
                        pinned=slot.declared_doctype is not None,
                    )
                ]
                files.append(slot)
                slot_index += 1
        # ``is_multi_file`` is recomputed after expansion so a single
        # uploaded ZIP that fans out to N members surfaces as multi-file.
        is_multi_file = len(files) > 1 or any(s.declared_doctype for s in files)
        ctx.metadata["is_multi_file"] = is_multi_file
        ctx.metadata["files_data"] = files
        return {"file_count": len(files), "is_multi_file": is_multi_file}

    async def _step_discover(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        """Per-file splitter: enumerate every distinct sub-document.

        Skipped for pinned files (caller already said what they are)
        and for single-page files (one segment is enough).
        """
        request: ExtractionRequest = ctx.metadata["request"]
        files: list[_FileSlot] = ctx.metadata["files_data"]

        async def _discover_one(slot: _FileSlot) -> None:
            if slot.declared_doctype is not None or slot.page_count <= 1:
                return
            try:
                result = await self._splitter.discover(
                    document_bytes=slot.document_bytes,
                    media_type=slot.media_type,
                    page_count=slot.page_count,
                    targets=request.docs,
                    intention=request.intention,
                    model=ctx.metadata["model_id"],
                )
            except Exception as exc:  # noqa: BLE001
                self._record_error(ctx, "discover", "SPLITTER_ERROR", exc, doc_type=slot.filename)
                return
            slot.segments = [
                _Segment(
                    file_index=slot.file_index,
                    filename=slot.filename,
                    media_type=slot.media_type,
                    page_start=seg.page_start,
                    page_end=seg.page_end,
                    file_page_count=slot.page_count,
                    provisional_type=seg.provisional_type,
                    description=seg.description,
                    segmentation_confidence=seg.confidence,
                )
                for seg in result.segments
            ] or slot.segments  # keep the default segment if the splitter came back empty

        await asyncio.gather(*(_discover_one(s) for s in files))
        total = sum(len(s.segments) for s in files)
        return {"segments": total}

    async def _step_classifier(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        """Per-segment classifier: pick a declared DocSpec for each segment."""
        request: ExtractionRequest = ctx.metadata["request"]
        files: list[_FileSlot] = ctx.metadata["files_data"]
        docs_by_type: dict[str, DocSpec] = {d.docType.documentType: d for d in request.docs}

        # If there's exactly one declared DocSpec, every unpinned, unmatched
        # segment is implicitly that one -- no LLM call needed.
        if len(request.docs) == 1:
            only = request.docs[0].docType.documentType
            for slot in files:
                for seg in slot.segments:
                    if seg.resolved_doctype is None:
                        seg.resolved_doctype = only
            return {"classified": 0, "implicit": True}

        # Collect every segment that still needs a doctype (i.e. not pinned).
        targets: list[tuple[_FileSlot, _Segment]] = []
        for slot in files:
            for seg in slot.segments:
                if seg.resolved_doctype is None:
                    targets.append((slot, seg))
        if not targets:
            return {"classified": 0}

        async def _classify_one(slot: _FileSlot, seg: _Segment) -> None:
            # Use the segment slice when we have more than one segment in the
            # file -- otherwise the whole file is fine. Slicing fails closed:
            # if it raises we fall back to the whole file bytes.
            bytes_for_seg, media_type = _slice_for_segment(slot, seg, ctx, self)
            try:
                result = await self._classifier.classify(
                    document_bytes=bytes_for_seg,
                    media_type=media_type,
                    filename=slot.filename,
                    candidates=request.docs,
                    intention=request.intention,
                    model=ctx.metadata["model_id"],
                )
                seg.classification = result
                if result.matched and result.document_type in docs_by_type:
                    seg.resolved_doctype = result.document_type
                else:
                    seg.resolved_doctype = None
            except Exception as exc:  # noqa: BLE001
                self._record_error(
                    ctx,
                    "classify",
                    "CLASSIFIER_ERROR",
                    exc,
                    doc_type=slot.filename,
                )
                seg.classification = ClassificationResult(
                    document_type=UNMATCHED, matched=False, notes=str(exc)[:200]
                )
                seg.resolved_doctype = None

        await asyncio.gather(*(_classify_one(slot, seg) for slot, seg in targets))
        matched = sum(1 for _, s in targets if s.resolved_doctype)
        return {"classified": matched, "unmatched": len(targets) - matched}

    async def _step_plan_tasks(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        """Build the flat list of (segment, DocSpec) extraction tasks."""
        request: ExtractionRequest = ctx.metadata["request"]
        files: list[_FileSlot] = ctx.metadata["files_data"]
        docs_by_type: dict[str, DocSpec] = {d.docType.documentType: d for d in request.docs}
        tasks: list[_ExtractionTask] = []
        unmatched_segments: list[_Segment] = list(ctx.metadata.get("unmatched_segments", []))

        for slot in files:
            for seg_index, seg in enumerate(slot.segments):
                # Unresolved segment -> nothing to extract; route to additional.
                if seg.resolved_doctype is None:
                    unmatched_segments.append(seg)
                    continue
                doc_spec = docs_by_type.get(seg.resolved_doctype)
                if doc_spec is None:
                    # Caller pinned (or classifier returned) a doctype that
                    # is not declared in ``docs[]``. The request validator
                    # rejects unknown pins up-front; this is a safety net.
                    unmatched_segments.append(seg)
                    continue
                slice_bytes, slice_pages = self._slice_segment_bytes(slot, seg, ctx)
                tasks.append(
                    _ExtractionTask(
                        task_id=f"file{slot.file_index}/seg{seg_index}/{seg.resolved_doctype}",
                        segment=seg,
                        doc_spec=doc_spec,
                        slice_bytes=slice_bytes,
                        slice_pages=slice_pages,
                    )
                )

        ctx.metadata["tasks"] = tasks
        ctx.metadata["unmatched_segments"] = unmatched_segments
        return {"task_count": len(tasks), "unmatched": len(unmatched_segments)}

    async def _step_extract(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        tasks: list[_ExtractionTask] = ctx.metadata["tasks"]

        async def _extract_one(task: _ExtractionTask) -> None:
            if not task.slice_bytes:
                return
            try:
                groups, used = await self._extractor.extract(
                    document_bytes=task.slice_bytes,
                    media_type=task.segment.media_type,
                    page_count=task.slice_pages,
                    doc=task.doc_spec,
                    intention=request.intention,
                    language_hint=request.options.language_hint,
                    model=ctx.metadata["model_id"],
                )
                task.extracted_groups = groups
                task.model_used = used
            except Exception as exc:  # noqa: BLE001
                self._record_error(ctx, "extractor", "EXTRACTOR_ERROR", exc, doc_type=task.task_id)

        await asyncio.gather(*(_extract_one(t) for t in tasks))
        return {"docs_extracted": sum(1 for t in tasks if t.extracted_groups)}

    async def _step_bbox_validation(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        tasks: list[_ExtractionTask] = ctx.metadata["tasks"]
        for task in tasks:
            if task.extracted_groups:
                self._bbox_validator.validate_groups(task.extracted_groups)
        return {"validated": True}

    async def _step_bbox_refine(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        """Replace LLM-estimated bboxes with grounded ones (PDF text / OCR).

        Runs per task (one per resolved (segment, DocSpec) pair) so a
        single request that fans out across multiple files / DocSpecs
        gets refined per source. Tasks with no extracted groups or no
        bytes are skipped. Failures degrade gracefully -- the LLM bbox
        stays in place and the pipeline continues.
        """
        request: ExtractionRequest = ctx.metadata["request"]
        tasks: list[_ExtractionTask] = ctx.metadata["tasks"]
        totals = {"fields": 0, "grounded_pdf_text": 0, "grounded_ocr": 0, "kept_llm": 0}

        async def _refine_one(task: _ExtractionTask) -> None:
            if not task.extracted_groups or not task.slice_bytes:
                return
            try:
                summary = await self._bbox_refiner.refine(
                    document_bytes=task.slice_bytes,
                    media_type=task.segment.media_type,
                    page_count=task.slice_pages,
                    groups=task.extracted_groups,
                    language_hint=request.options.language_hint,
                )
                totals["fields"] += summary.fields_seen
                totals["grounded_pdf_text"] += summary.grounded_pdf_text
                totals["grounded_ocr"] += summary.grounded_ocr
                totals["kept_llm"] += summary.kept_llm
            except Exception as exc:  # noqa: BLE001 -- non-fatal degrade
                self._record_error(ctx, "bbox_refine", "BBOX_REFINE_ERROR", exc, doc_type=task.task_id)

        await asyncio.gather(*(_refine_one(t) for t in tasks))
        return totals

    async def _step_field_validation(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        tasks: list[_ExtractionTask] = ctx.metadata["tasks"]
        for task in tasks:
            self._field_validator.validate(task.doc_spec.fieldGroups, task.extracted_groups)
        return {"validated": True}

    async def _step_visual_authenticity(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        tasks: list[_ExtractionTask] = ctx.metadata["tasks"]

        async def _check_one(task: _ExtractionTask) -> None:
            if not task.doc_spec.validators.visual or not task.slice_bytes:
                return
            try:
                outcomes = await self._visual_checker.check(
                    document_bytes=task.slice_bytes,
                    media_type=task.segment.media_type,
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

    async def _step_content_authenticity(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        tasks: list[_ExtractionTask] = ctx.metadata["tasks"]

        async def _audit_one(task: _ExtractionTask) -> None:
            if not task.slice_bytes:
                return
            try:
                task.content = await self._content_checker.check(
                    document_bytes=task.slice_bytes,
                    media_type=task.segment.media_type,
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
            if not task.extracted_groups or not task.slice_bytes:
                return
            try:
                await self._judge.judge(
                    document_bytes=task.slice_bytes,
                    media_type=task.segment.media_type,
                    doc=task.doc_spec,
                    extracted_groups=task.extracted_groups,
                    intention=request.intention,
                    model=ctx.metadata["model_id"],
                )
            except Exception as exc:  # noqa: BLE001
                self._record_error(ctx, "judge", "JUDGE_ERROR", exc, doc_type=task.task_id)

        await asyncio.gather(*(_judge_one(t) for t in tasks))
        return {"judged": True}

    async def _step_judge_escalation(self, ctx: PipelineContext, _inputs: dict[str, Any]) -> Any:
        request: ExtractionRequest = ctx.metadata["request"]
        tasks: list[_ExtractionTask] = ctx.metadata["tasks"]
        # The escalator was built around per-doc maps keyed by a stable id.
        # Use ``task_id`` so each (segment, DocSpec) re-run is independent.
        per_doc_extracted = {t.task_id: t.extracted_groups for t in tasks}
        per_doc_inputs = {
            t.task_id: (
                t.slice_bytes,
                t.segment.media_type,
                t.slice_pages,
                t.doc_spec,
                _segment_as_split(t.segment),
            )
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
                if info.accepted:
                    for t in tasks:
                        t.extracted_groups = ctx.metadata["per_doc_extracted"][t.task_id]
                        t.model_used = ctx.metadata["per_doc_model_used"].get(t.task_id, t.model_used)
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

        # The rule engine takes per-doctype maps. Group by doctype across
        # files and segments -- multiple segments of the same doctype
        # contribute their field groups to the same bucket.
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
    # Helpers
    # ------------------------------------------------------------------

    def _slice_segment_bytes(
        self,
        slot: _FileSlot,
        seg: _Segment,
        ctx: PipelineContext,
    ) -> tuple[bytes, int]:
        """Return the (bytes, page_count) the extractor should see for ``seg``.

        - Whole-file segment -> the full file bytes (no slicing).
        - Page-range segment on a PDF -> slice with pypdf.
        - Page-range segment on a non-PDF -> the full bytes (we can't slice
          a single image into sub-pages).

        Failures are recorded and the caller falls back to the full bytes.
        """
        is_whole_file = seg.page_start == 1 and seg.page_end == slot.page_count
        if is_whole_file or slot.media_type != "application/pdf":
            return slot.document_bytes, slot.page_count
        try:
            sliced = slice_pdf(
                slot.document_bytes,
                PageRange(start=seg.page_start, end=seg.page_end),
            )
            return sliced, seg.page_end - seg.page_start + 1
        except Exception as exc:  # noqa: BLE001
            self._record_error(
                ctx,
                "pdf_slicer",
                "SLICE_ERROR",
                exc,
                doc_type=f"{slot.filename}:{seg.page_start}-{seg.page_end}",
            )
            return slot.document_bytes, slot.page_count

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
        *,
        pipeline_result: Any = None,
    ) -> ExtractionResult:
        files_data: list[_FileSlot] = ctx.metadata.get("files_data", [])
        tasks: list[_ExtractionTask] = ctx.metadata.get("tasks", [])
        rule_results = ctx.metadata.get("rule_results", [])
        is_multi_file: bool = ctx.metadata.get("is_multi_file", False)
        unmatched_segments: list[_Segment] = ctx.metadata.get("unmatched_segments", [])

        files_info = [_file_info(slot) for slot in files_data]
        # Legacy ``document`` field for the single-file request shape.
        document_info = files_info[0] if (files_info and not is_multi_file) else None

        # Resolve the model field by aggregating what the extractor actually
        # used across tasks (handles fallback + escalation).
        used_models = {t.model_used for t in tasks if t.model_used}
        if used_models:
            model_id = ",".join(sorted(used_models)) if len(used_models) > 1 else next(iter(used_models))

        documents: list[ExtractedDocument] = [
            ExtractedDocument(
                document_type=task.doc_spec.docType.documentType,
                missing=False,
                pages=_pages_range(task.segment.page_start, task.segment.page_end),
                description=_segment_description(task.segment, task.doc_spec),
                confidence=_segment_confidence(task.segment),
                fields=task.extracted_groups,
                authenticity=DocumentAuthenticity(visual=task.visual, content=task.content),
                source_file=task.segment.filename if is_multi_file else None,
            )
            for task in tasks
        ]

        # Unmatched / unroutable segments
        additional_documents = [
            ExtractedDocument(
                document_type=UNMATCHED,
                missing=False,
                pages=_pages_range(seg.page_start, seg.page_end),
                description=(seg.classification.description if seg.classification else seg.description),
                confidence=(
                    seg.classification.confidence if seg.classification else seg.segmentation_confidence
                ),
                notes=(seg.classification.notes if seg.classification else None),
                source_file=seg.filename if is_multi_file else None,
            )
            for seg in unmatched_segments
        ]

        escalation: EscalationInfo | None = ctx.metadata.get("escalation")
        usage_breakdown = _usage_breakdown(
            request_id=str(request.request_id),
            pipeline_result=pipeline_result,
        )
        trace = _trace_entries(pipeline_result)
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
            usage=usage_breakdown,
            trace=trace,
        )


# ---------------------------------------------------------------------------
# Stateless helpers
# ---------------------------------------------------------------------------


def _pages_range(start: int | None, end: int | None) -> list[int]:
    if start is None or end is None or end < start:
        return []
    return list(range(start, end + 1))


def _segment_description(seg: _Segment, doc_spec: DocSpec) -> str:
    if seg.classification and seg.classification.description:
        return seg.classification.description
    if seg.description:
        return seg.description
    return doc_spec.docType.description or ""


def _segment_confidence(seg: _Segment) -> float:
    if seg.classification and seg.classification.matched:
        return seg.classification.confidence
    if seg.pinned:
        return 1.0
    return seg.segmentation_confidence


def _usage_breakdown(*, request_id: str, pipeline_result: Any) -> UsageBreakdown | None:
    """Map the framework's UsageSummary into our public DTO.

    Prefers ``pipeline_result.usage`` (already aggregated by the engine
    for this correlation id). Falls back to a fresh
    ``default_usage_tracker.get_summary_for_correlation`` query so we
    still report something useful even if the engine result is missing
    or partial. Returns ``None`` when no records exist for the request.
    """
    summary = None
    if pipeline_result is not None and getattr(pipeline_result, "usage", None) is not None:
        summary = pipeline_result.usage
    if summary is None:
        try:
            from fireflyframework_agentic.observability.usage import (
                default_usage_tracker,
            )

            summary = default_usage_tracker.get_summary_for_correlation(request_id)
        except Exception:  # noqa: BLE001
            return None
    if summary is None or getattr(summary, "record_count", 0) == 0:
        return None
    # Cache totals are not exposed on UsageSummary -- recompute from the
    # raw records when available so we don't lose cache hits in the
    # response.
    cache_creation, cache_read = 0, 0
    try:
        from fireflyframework_agentic.observability.usage import default_usage_tracker

        for r in default_usage_tracker.records:
            if r.correlation_id == request_id:
                cache_creation += r.cache_creation_tokens
                cache_read += r.cache_read_tokens
    except Exception:  # noqa: BLE001
        pass
    return UsageBreakdown(
        total_input_tokens=summary.total_input_tokens,
        total_output_tokens=summary.total_output_tokens,
        total_tokens=summary.total_tokens,
        total_cost_usd=summary.total_cost_usd,
        total_requests=summary.total_requests,
        total_latency_ms=summary.total_latency_ms,
        record_count=summary.record_count,
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
        by_agent=dict(summary.by_agent),
        by_model=dict(summary.by_model),
    )


def _trace_entries(pipeline_result: Any) -> list[TraceEntry]:
    """Convert the engine's ExecutionTraceEntry list into our DTO."""
    if pipeline_result is None or not getattr(pipeline_result, "execution_trace", None):
        return []
    entries: list[TraceEntry] = []
    for entry in pipeline_result.execution_trace:
        try:
            delta_ms = (entry.completed_at - entry.started_at).total_seconds() * 1000
        except Exception:  # noqa: BLE001
            delta_ms = 0.0
        entries.append(
            TraceEntry(
                node=entry.node_id,
                started_at=entry.started_at,
                completed_at=entry.completed_at,
                latency_ms=round(delta_ms, 2),
                status=entry.status,
            )
        )
    return entries


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


def _file_info(slot: _FileSlot) -> DocumentInfo:
    """Top-level ``DocumentInfo`` summary for the per-file response field.

    For files with a single segment we surface the segment's classifier
    verdict directly. For files split into multiple segments,
    ``document_type`` is left ``null`` and ``classification`` is null --
    the per-segment outcomes live on ``documents[]`` and
    ``additional_documents[]``.
    """
    one_segment = len(slot.segments) == 1
    doc_type = slot.segments[0].resolved_doctype if one_segment else None
    classification = _classification_info(slot.segments[0].classification) if one_segment else None
    return DocumentInfo(
        filename=slot.filename,
        media_type=slot.media_type,
        page_count=slot.page_count,
        bytes=len(slot.document_bytes),
        document_type=doc_type,
        classification=classification,
    )


def _segment_as_split(seg: _Segment) -> DiscoveredSegment:
    """Adapter so the legacy JudgeEscalator input tuple keeps working."""
    return DiscoveredSegment(
        page_start=seg.page_start,
        page_end=seg.page_end,
        provisional_type=seg.provisional_type,
        description=seg.description,
        confidence=seg.segmentation_confidence,
    )


def _slice_for_segment(
    slot: _FileSlot,
    seg: _Segment,
    ctx: PipelineContext,
    orchestrator: PipelineOrchestrator,
) -> tuple[bytes, str]:
    """Return (bytes, media_type) for the classifier of ``seg``.

    When the file has only one segment we pass the original bytes; for
    multi-segment files we slice the PDF down to the segment's pages so
    the classifier sees only that document.
    """
    if len(slot.segments) <= 1:
        return slot.document_bytes, slot.media_type
    bytes_for_seg, _pages = orchestrator._slice_segment_bytes(slot, seg, ctx)  # noqa: SLF001
    return bytes_for_seg, slot.media_type
