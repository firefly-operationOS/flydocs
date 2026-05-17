# Copyright 2026 Firefly Software Solutions Inc
"""flydocs -- Intelligent Document Processing service.

Pure multimodal extraction of structured fields with bounding boxes
from PDFs, images, and any format an LLM provider accepts as
:class:`BinaryContent`. The service exposes a synchronous REST API
(`POST /api/v1/extract`) and an async queue-backed API
(`POST /api/v1/jobs`), both backed by the same pipeline orchestrator
running on top of ``fireflyframework-pyfly`` (DI, CQRS, web) and
``fireflyframework-agentic`` (`PipelineEngine`, `FireflyAgent`,
`PromptRegistry`).
"""

__version__ = "0.1.0"
