# Copyright 2026 Firefly Software Solutions Inc
"""``@configuration`` class -- exposes every cross-cutting service as a pyfly bean.

Pyfly's container scans this module, sees the ``@configuration`` class,
instantiates it once, then calls each ``@bean`` method to produce the
beans. The return-type annotations determine the bean's interface, so
constructor-injection works on every consumer (controller, command /
query handler, worker).

The configuration is the **single** declaration point for everything
that is not picked up by a stereotype decorator
(``@service``/``@rest_controller``/``@command_handler``/``@query_handler``):

* Configuration: :class:`IDPSettings`
* Infrastructure: :class:`ExtractionRepository`, :class:`WebhookPublisher`.
  The :class:`pyfly.eda.EventPublisher` is provided upstream by
  :class:`pyfly.eda.auto_configuration.EdaAutoConfiguration` (Postgres
  outbox by default; see ``pyfly.yaml``).
* Health: SQLAlchemy + EDA indicators wired into pyfly's
  :class:`~pyfly.actuator.health.HealthAggregator`
* Prompt management: :class:`PromptCatalog`
* LLM stages: extractor, splitter, field validator, visual / content
  authenticity, judge, rule engine
* Pipeline orchestrator
* Async worker: :class:`JobWorker`
"""

from __future__ import annotations

from fireflyframework_agentic.content.binary import (
    BinaryConfig,
    BinaryNormalizer,
    OfficeConverter,
    build_office_converter,
)
from pyfly.container import bean, configuration
from pyfly.data.relational.health import SqlAlchemyHealthIndicator
from pyfly.eda import EventPublisher

from flydocs.config import IDPSettings, get_settings
from flydocs.core.services.authenticity import (
    ContentAuthenticityChecker,
    VisualAuthenticityChecker,
)
from flydocs.core.services.bbox import (
    BboxRefiner,
    BboxValidator,
    BboxValueMatcher,
    DoclingOcrEngine,
    HybridValueMatcher,
    LlmValueMatcher,
    NoneOcrEngine,
    OcrEngine,
    TesseractOcrEngine,
    ValueMatcher,
)
from flydocs.core.services.classification import DocumentClassifier
from flydocs.core.services.escalation import JudgeEscalator
from flydocs.core.services.extraction.extractor import MultimodalExtractor
from flydocs.core.services.extraction.prompts import PromptCatalog
from flydocs.core.services.extraction.text_anchor import (
    DoclingTextAnchor,
    NoOpTextAnchor,
    TextAnchor,
)
from flydocs.core.services.judge import Judge
from flydocs.core.services.pipeline import PipelineOrchestrator
from flydocs.core.services.rules import RuleEngine
from flydocs.core.services.splitting import DocumentSplitter
from flydocs.core.services.transformations import LlmTransformer, TransformationEngine
from flydocs.core.services.validation import FieldValidator, RequestValidator
from flydocs.core.services.webhook import WebhookPublisher
from flydocs.core.services.workers.job_worker import ExtractionWorker
from flydocs.models.repositories import ExtractionRepository


@configuration
class IDPCoreConfiguration:
    """Wiring for everything outside the pyfly stereotype decorators."""

    # ------------------------------------------------------------------
    # Configuration + infrastructure
    # ------------------------------------------------------------------

    @bean
    def settings(self) -> IDPSettings:
        return get_settings()

    @bean
    def repository(self, settings: IDPSettings) -> ExtractionRepository:
        return ExtractionRepository.from_url(settings.database_url)

    @bean
    def webhook(self, settings: IDPSettings) -> WebhookPublisher:
        return WebhookPublisher(
            timeout_s=settings.webhook_timeout_s,
            max_attempts=settings.webhook_max_attempts,
            hmac_secret=settings.webhook_hmac_secret,
        )

    # ------------------------------------------------------------------
    # Health indicators -- wired into pyfly's actuator so /actuator/health
    # actually reflects what the service can talk to.
    #
    # The EventPublisher indicator is registered upstream by pyfly's
    # ``EdaHealthAutoConfiguration``; this configuration only adds the
    # database probe so it can use the repository's engine without
    # pulling SQLAlchemy plumbing into the actuator module.
    # ------------------------------------------------------------------

    @bean(name="database_health")
    def database_health(self, repository: ExtractionRepository) -> SqlAlchemyHealthIndicator:
        return SqlAlchemyHealthIndicator(repository.engine)

    # ------------------------------------------------------------------
    # Prompt management -- one catalog, every LLM stage takes a template
    # from it. This keeps prompt text out of Python code paths.
    # ------------------------------------------------------------------

    @bean
    def prompt_catalog(self) -> PromptCatalog:
        return PromptCatalog.from_resources()

    # ------------------------------------------------------------------
    # LLM stages -- each receives its prompt template through DI.
    # ------------------------------------------------------------------

    @bean
    def text_anchor(self, settings: IDPSettings) -> TextAnchor:
        """Optional pre-extraction text-anchor service.

        ``none`` (default) returns ``None`` for every call -- zero
        overhead. ``docling`` runs Docling over the document so the
        extractor can splice a Markdown view into the user prompt.
        See :class:`DoclingTextAnchor` for the trade-offs.
        """
        kind = (settings.extraction_text_anchor or "none").lower()
        if kind in {"none", "", "off"}:
            return NoOpTextAnchor()
        if kind == "docling":
            return DoclingTextAnchor(settings=settings)
        raise ValueError(
            f"unknown FLYDOCS_EXTRACTION_TEXT_ANCHOR={settings.extraction_text_anchor!r}; "
            "expected 'none' or 'docling'"
        )

    @bean
    def extractor(
        self,
        settings: IDPSettings,
        prompts: PromptCatalog,
        text_anchor: TextAnchor,
    ) -> MultimodalExtractor:
        return MultimodalExtractor(
            template=prompts.extract,
            retry_arrays_template=prompts.extract_retry_arrays,
            model=settings.model,
            fallback_model=settings.fallback_model,
            text_anchor=text_anchor,
        )

    @bean
    def splitter(self, settings: IDPSettings, prompts: PromptCatalog) -> DocumentSplitter:
        return DocumentSplitter(template=prompts.splitter, model=settings.model)

    @bean
    def classifier(self, settings: IDPSettings, prompts: PromptCatalog) -> DocumentClassifier:
        return DocumentClassifier(template=prompts.classifier, model=settings.model)

    @bean
    def field_validator(self) -> FieldValidator:
        return FieldValidator()

    @bean
    def bbox_validator(self) -> BboxValidator:
        """Geometric bbox hallucination check -- runs after extraction."""
        return BboxValidator()

    # ------------------------------------------------------------------
    # Bbox refinement -- grounded coordinates from PDF text layer + OCR.
    #
    # The OCR engine is pluggable behind ``OcrEngine``. The default
    # ``none`` adapter is a no-op (image pages keep the LLM bbox); real
    # engines (Paddle / Mistral / Tesseract) land in follow-up adapters
    # that swap in here without touching the BboxRefiner contract.
    # ------------------------------------------------------------------

    @bean
    def ocr_engine(self, settings: IDPSettings) -> OcrEngine:
        kind = (settings.bbox_refine_ocr_engine or "tesseract").lower()
        if kind == "tesseract":
            return TesseractOcrEngine(settings=settings)
        if kind == "none":
            return NoneOcrEngine()
        if kind == "docling":
            # Layout-aware adapter. Pulls in PyTorch + HF models at first
            # use; install via the ``docling`` extra. See
            # ``DoclingOcrEngine`` docstring for the trade-offs.
            return DoclingOcrEngine(settings=settings)
        # Future: ``paddle`` / ``mistral`` adapters route here.
        raise ValueError(
            f"unknown FLYDOCS_BBOX_REFINE_OCR_ENGINE={settings.bbox_refine_ocr_engine!r}; "
            "expected 'tesseract', 'docling', or 'none' "
            "(paddle / mistral adapters not yet bundled)"
        )

    @bean
    def bbox_value_matcher(self, settings: IDPSettings, prompts: PromptCatalog) -> BboxValueMatcher:
        """Strategy that grounds extracted values against the word stream.

        Defaults to ``hybrid``: rapidfuzz first (deterministic, free,
        millisecond-scale), LLM only for the residual that fuzzy could
        not resolve. This combination grounds 70-90% of fields without
        an LLM call while still handling spelled-out numbers, date
        format variants, and multilingual quirks via the LLM fallback.
        ``llm`` and ``fuzzy`` remain available for callers that want a
        single strategy with no cascade.
        """
        kind = (settings.bbox_refine_matcher or "hybrid").lower()
        if kind == "hybrid":
            return HybridValueMatcher(
                fuzzy=ValueMatcher(settings=settings),
                llm=LlmValueMatcher(
                    template=prompts.bbox_matcher,
                    model=settings.model,
                    threshold=settings.bbox_refine_threshold,
                ),
            )
        if kind == "llm":
            return LlmValueMatcher(
                template=prompts.bbox_matcher,
                model=settings.model,
                threshold=settings.bbox_refine_threshold,
            )
        if kind == "fuzzy":
            return ValueMatcher(settings=settings)
        raise ValueError(
            f"unknown FLYDOCS_BBOX_REFINE_MATCHER={settings.bbox_refine_matcher!r}; "
            "expected 'hybrid', 'llm', or 'fuzzy'"
        )

    @bean
    def request_validator(self) -> RequestValidator:
        """Pre-flight semantic checker on the public ExtractionRequest."""
        return RequestValidator()

    # ------------------------------------------------------------------
    # Binary normalization
    #
    # Office conversion is pluggable behind the OfficeConverter
    # protocol. The default ``gotenberg`` adapter keeps the runtime
    # container distroless-friendly by delegating to a Gotenberg
    # sidecar; ``libreoffice`` falls back to an in-container subprocess
    # for slim/dev images that bundle ``soffice``.
    # ------------------------------------------------------------------

    @bean
    def binary_config(self, settings: IDPSettings) -> BinaryConfig:
        """Map flydocs settings onto the framework's host-agnostic BinaryConfig.

        ``wrap_text_as_pdf`` is True: plain text / markdown / CSV are rendered
        to PDF via the office converter so the multimodal LLM receives
        renderable bytes (flydocs is pure-multimodal, no text loader).
        """
        return BinaryConfig(
            normalize_enabled=settings.binary_normalize_enabled,
            max_recursion_depth=settings.binary_max_recursion_depth,
            max_expanded_files=settings.binary_max_expanded_files,
            wrap_text_as_pdf=True,
            office_converter=settings.office_converter,
            gotenberg_url=settings.gotenberg_url,
            gotenberg_timeout_s=float(settings.gotenberg_timeout_s),
            libreoffice_path=settings.binary_libreoffice_path,
            libreoffice_timeout_s=float(settings.binary_libreoffice_timeout_s),
        )

    @bean
    def office_converter(self, binary_config: BinaryConfig) -> OfficeConverter:
        kind = (binary_config.office_converter or "gotenberg").lower()
        if kind not in {"gotenberg", "libreoffice"}:
            raise ValueError(
                f"unknown FLYDOCS_OFFICE_CONVERTER={binary_config.office_converter!r}; "
                "expected 'gotenberg' or 'libreoffice'"
            )
        return build_office_converter(binary_config)

    @bean
    def binary_normalizer(
        self,
        binary_config: BinaryConfig,
        office_converter: OfficeConverter,
    ) -> BinaryNormalizer:
        # The framework BinaryNormalizer constructs its own PdfGuard /
        # ImageNormalizer / ArchiveUnpacker / EmailUnpacker from the config;
        # only the pluggable OfficeConverter is injected.
        return BinaryNormalizer(config=binary_config, office=office_converter)

    @bean
    def visual_checker(self, settings: IDPSettings, prompts: PromptCatalog) -> VisualAuthenticityChecker:
        return VisualAuthenticityChecker(template=prompts.visual_authenticity, model=settings.model)

    @bean
    def content_checker(self, settings: IDPSettings, prompts: PromptCatalog) -> ContentAuthenticityChecker:
        return ContentAuthenticityChecker(template=prompts.content_authenticity, model=settings.model)

    @bean
    def judge(self, settings: IDPSettings, prompts: PromptCatalog) -> Judge:
        return Judge(template=prompts.judge, model=settings.model)

    @bean
    def llm_transformer(self, settings: IDPSettings, prompts: PromptCatalog) -> LlmTransformer:
        """Free-form post-extraction transformer.

        Needs explicit ``@bean`` construction because it depends on
        a ``PromptTemplate`` (resolved through :class:`PromptCatalog`)
        and the default model id — neither of which pyfly's autoscan
        can resolve by type alone. :class:`TransformationEngine` is a
        ``@service`` that autowires this bean alongside the
        ``EntityResolutionTransformer``.
        """
        return LlmTransformer(template=prompts.transform, model=settings.model)

    @bean
    def rule_engine(self, settings: IDPSettings, prompts: PromptCatalog) -> RuleEngine:
        return RuleEngine(template=prompts.rule_engine, model=settings.model)

    @bean
    def judge_escalator(
        self,
        extractor: MultimodalExtractor,
        judge: Judge,
        settings: IDPSettings,
    ) -> JudgeEscalator:
        """Stronger-model re-run when the judge's failure rate exceeds threshold."""
        return JudgeEscalator(
            extractor=extractor,
            judge=judge,
            default_threshold=settings.escalation_threshold,
            default_model=settings.escalation_model,
        )

    # ------------------------------------------------------------------
    # Orchestrator + async worker
    # ------------------------------------------------------------------

    @bean
    def orchestrator(
        self,
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
        transformation_engine: TransformationEngine,
        settings: IDPSettings,
    ) -> PipelineOrchestrator:
        return PipelineOrchestrator(
            extractor=extractor,
            splitter=splitter,
            classifier=classifier,
            field_validator=field_validator,
            bbox_validator=bbox_validator,
            bbox_refiner=bbox_refiner,
            binary_normalizer=binary_normalizer,
            visual_checker=visual_checker,
            content_checker=content_checker,
            judge=judge,
            rule_engine=rule_engine,
            judge_escalator=judge_escalator,
            transformation_engine=transformation_engine,
            settings=settings,
            default_model=settings.model,
        )

    # ``ExtractionWorker`` is NOT a bean. It depends on the
    # :class:`EventPublisher` produced by pyfly's auto-configuration,
    # which is registered AFTER user @configuration classes are
    # processed. The CLI's ``flydocs worker`` command builds the
    # worker manually post-startup so the ordering is correct.

    def _build_extraction_worker(  # noqa: PLR0913 - explicit injection for the CLI helper
        self,
        orchestrator: PipelineOrchestrator,
        repository: ExtractionRepository,
        event_publisher: EventPublisher,
        webhook: WebhookPublisher,
        settings: IDPSettings,
    ) -> ExtractionWorker:
        return ExtractionWorker(
            orchestrator=orchestrator,
            repository=repository,
            event_publisher=event_publisher,
            webhook=webhook,
            settings=settings,
        )
