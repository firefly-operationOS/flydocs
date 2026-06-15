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

# syntax=docker/dockerfile:1.7
#
# Multi-stage Docker build for flydocs.
#
# pyfly + fireflyframework-agentic are consumed as git dependencies
# (pinned tags in pyproject.toml's [tool.uv.sources]), so ``uv sync``
# resolves them straight from GitHub at build time. The build therefore
# needs network access + git (both provided in the builder stage) but no
# sibling checkout on disk and no BuildKit named contexts.
#
# Usage:
#     docker buildx build --tag flydocs:latest .
#
# See docker-compose.yml for the canonical invocation.

ARG PYTHON_VERSION=3.13
# Opt-in: bake the optional ``docling`` extra into the image so the
# layout-aware OCR engine + Markdown text-anchor are available
# without a runtime ``pip install``. Default is OFF -- the slim image
# stays small (PyTorch + HF models add ~2.5 GB). Build the docling
# variant with:
#
#     docker buildx build --build-arg WITH_DOCLING=true ...
#
# In CI both variants are published as separate tags (see
# ``.github/workflows/docker-publish.yaml``).
ARG WITH_DOCLING=false

# ---- Stage 1: builder -----------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder
ARG WITH_DOCLING

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy

# Stage the project manifests for layer caching. README.md is required by
# hatchling because pyproject.toml declares ``readme = "README.md"``.
# pyfly + fireflyframework-agentic are git sources in pyproject.toml
# (pinned tags), so the ``uv sync`` calls below fetch them from GitHub --
# no sibling sources are staged into the image.
WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
COPY README.md      /app/README.md
COPY uv.lock*       /app/

# Compose the optional-extras list once so both ``uv sync`` calls stay
# in lock-step. Docling adds PyTorch + Hugging Face models; everything
# else is in the default deps already.
RUN if [ "${WITH_DOCLING}" = "true" ]; then \
        echo "--extra docling" > /tmp/uv-extras; \
    else \
        : > /tmp/uv-extras; \
    fi

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --no-dev --no-editable $(cat /tmp/uv-extras)

# Copy the application source + migrations and finalise the install.
COPY src/         /app/src/
COPY migrations/  /app/migrations/
COPY alembic.ini  /app/alembic.ini
COPY pyfly.yaml   /app/pyfly.yaml
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-editable $(cat /tmp/uv-extras)


# ---- Stage 2: runtime -----------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime
ARG WITH_DOCLING

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:${PATH}"
# Surface the build flag for boot-time logging + actuator info.
ENV FLYDOCS_IMAGE_VARIANT=${WITH_DOCLING:+docling}

# System libs required by the binary normalizer's image adapters:
# * ``libheif1``                                -- pillow-heif (HEIC / HEIF / AVIF)
# * ``libcairo2`` / ``libpango*`` / ``libgdk-pixbuf-2.0-0`` -- cairosvg (SVG)
#
# Office conversion (DOCX/XLSX/PPTX/RTF/HTML) goes through the Gotenberg
# sidecar by default (``FLYDOCS_OFFICE_CONVERTER=gotenberg``), so
# ``soffice`` is intentionally NOT installed here -- it would bloat the
# image by ~700MB and is not needed when running against the canonical
# compose stack. Operators who want the in-container subprocess path
# (``FLYDOCS_OFFICE_CONVERTER=libreoffice``) extend this Dockerfile
# with ``libreoffice-core`` + ``fonts-noto-cjk`` etc. on their side.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        libheif1 \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        tesseract-ocr \
        tesseract-ocr-spa \
        tesseract-ocr-eng \
        tesseract-ocr-fra \
        tesseract-ocr-deu \
        tesseract-ocr-ita \
        tesseract-ocr-por \
        tesseract-ocr-cat \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --shell /usr/sbin/nologin --no-create-home idp

WORKDIR /app
# Copy artefacts as the unprivileged ``idp`` user so the runtime always has
# read access to bundled resources (e.g. resources/prompts/*.yaml) regardless
# of whatever host umask produced the source tree.
COPY --from=builder --chown=idp:idp /app/.venv         /app/.venv
COPY --from=builder --chown=idp:idp /app/src           /app/src
COPY --from=builder --chown=idp:idp /app/migrations    /app/migrations
COPY --from=builder --chown=idp:idp /app/alembic.ini   /app/alembic.ini
COPY --from=builder --chown=idp:idp /app/pyfly.yaml    /app/pyfly.yaml
COPY --from=builder --chown=idp:idp /app/docker-entrypoint.sh /app/docker-entrypoint.sh
# Ensure files are world-readable inside the image -- mktemp/host umask
# sometimes ships 0600 files which then fail at boot under the idp user.
RUN find /app -type f -exec chmod a+r {} + \
    && find /app -type d -exec chmod a+rx {} + \
    && chmod a+x /app/docker-entrypoint.sh

ENV PYTHONPATH=/app/src

USER idp
# 8080 = business API (pyfly.server.port); 9090 = management (actuator + admin,
# pyfly.management.server.port) and the worker health server.
EXPOSE 8080 9090

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["serve"]
