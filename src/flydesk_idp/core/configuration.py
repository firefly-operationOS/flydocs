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
* Infrastructure: :class:`ExtractionJobRepository`, :class:`WebhookPublisher`.
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

from pyfly.container import bean, configuration
from pyfly.data.relational.health import SqlAlchemyHealthIndicator
from pyfly.eda import EventPublisher

from flydesk_idp.config import IDPSettings, get_settings
from flydesk_idp.core.services.authenticity import (
    ContentAuthenticityChecker,
    VisualAuthenticityChecker,
)
from flydesk_idp.core.services.bbox import (
    BboxRefiner,
    BboxValidator,
    BboxValueMatcher,
    LlmValueMatcher,
    NoneOcrEngine,
    OcrEngine,
    TesseractOcrEngine,
    ValueMatcher,
)
from flydesk_idp.core.services.binary import (
    ArchiveUnpacker,
    BinaryNormalizer,
    EmailUnpacker,
    GotenbergConverter,
    ImageNormalizer,
    LibreOfficeConverter,
    OfficeConverter,
    PdfGuard,
)
from flydesk_idp.core.services.classification import DocumentClassifier
from flydesk_idp.core.services.escalation import JudgeEscalator
from flydesk_idp.core.services.extraction.extractor import MultimodalExtractor
from flydesk_idp.core.services.extraction.prompts import PromptCatalog
from flydesk_idp.core.services.judge import Judge
from flydesk_idp.core.services.pipeline import PipelineOrchestrator
from flydesk_idp.core.services.rules import RuleEngine
from flydesk_idp.core.services.splitting import DocumentSplitter
from flydesk_idp.core.services.validation import FieldValidator, RequestValidator
from flydesk_idp.core.services.webhook import WebhookPublisher
from flydesk_idp.core.services.workers.job_worker import JobWorker
from flydesk_idp.models.repositories import ExtractionJobRepository


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
    def repository(self, settings: IDPSettings) -> ExtractionJobRepository:
        return ExtractionJobRepository.from_url(settings.database_url)

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
    def database_health(self, repository: ExtractionJobRepository) -> SqlAlchemyHealthIndicator:
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
    def extractor(self, settings: IDPSettings, prompts: PromptCatalog) -> MultimodalExtractor:
        return MultimodalExtractor(
            template=prompts.extract,
            model=settings.model,
            fallback_model=settings.fallback_model,
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
        # Future: ``paddle`` / ``mistral`` adapters route here.
        raise ValueError(
            f"unknown FLYDESK_IDP_BBOX_REFINE_OCR_ENGINE={settings.bbox_refine_ocr_engine!r}; "
            "expected 'tesseract' or 'none' (paddle / mistral adapters not yet bundled)"
        )

    @bean
    def bbox_value_matcher(self, settings: IDPSettings, prompts: PromptCatalog) -> BboxValueMatcher:
        """Strategy that grounds extracted values against the word stream.

        Defaults to the LLM-driven matcher (``llm``) -- generic,
        multilingual, no hardcoded variants. ``fuzzy`` selects the
        deterministic rapidfuzz fallback for callers that want zero
        LLM cost on the refine path.
        """
        kind = (settings.bbox_refine_matcher or "llm").lower()
        if kind == "llm":
            return LlmValueMatcher(
                template=prompts.bbox_matcher,
                model=settings.model,
                threshold=settings.bbox_refine_threshold,
            )
        if kind == "fuzzy":
            return ValueMatcher(settings=settings)
        raise ValueError(
            f"unknown FLYDESK_IDP_BBOX_REFINE_MATCHER={settings.bbox_refine_matcher!r}; "
            "expected 'llm' or 'fuzzy'"
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
    def office_converter(self, settings: IDPSettings) -> OfficeConverter:
        kind = (settings.office_converter or "gotenberg").lower()
        if kind == "libreoffice":
            return LibreOfficeConverter(settings=settings)
        if kind == "gotenberg":
            return GotenbergConverter(settings=settings)
        raise ValueError(
            f"unknown FLYDESK_IDP_OFFICE_CONVERTER={settings.office_converter!r}; "
            "expected 'gotenberg' or 'libreoffice'"
        )

    @bean
    def binary_normalizer(
        self,
        settings: IDPSettings,
        pdf_guard: PdfGuard,
        image_normalizer: ImageNormalizer,
        office_converter: OfficeConverter,
        archive_unpacker: ArchiveUnpacker,
        email_unpacker: EmailUnpacker,
    ) -> BinaryNormalizer:
        return BinaryNormalizer(
            settings=settings,
            pdf_guard=pdf_guard,
            image=image_normalizer,
            office=office_converter,
            archive=archive_unpacker,
            email_=email_unpacker,
        )

    # PdfGuard / ImageNormalizer / ArchiveUnpacker / EmailUnpacker carry
    # ``@service`` decorators -- pyfly autoscan picks them up via the
    # ``flydesk_idp.core`` scan_packages entry. They are listed here only
    # so the dependency graph is auditable from this single file.

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
            default_model=settings.model,
        )

    # ``JobWorker`` is NOT a bean. It depends on the
    # :class:`EventPublisher` produced by pyfly's auto-configuration,
    # which is registered AFTER user @configuration classes are
    # processed. The CLI's ``flydesk-idp worker`` command builds the
    # worker manually post-startup so the ordering is correct.

    def _build_job_worker(  # noqa: PLR0913 - explicit injection for the CLI helper
        self,
        orchestrator: PipelineOrchestrator,
        repository: ExtractionJobRepository,
        event_publisher: EventPublisher,
        webhook: WebhookPublisher,
        settings: IDPSettings,
    ) -> JobWorker:
        return JobWorker(
            orchestrator=orchestrator,
            repository=repository,
            event_publisher=event_publisher,
            webhook=webhook,
            settings=settings,
        )
