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

"""Widen ``extraction_jobs.status`` from ``varchar(16)`` to ``varchar(24)``.

Revision ID: 0003_widen_job_status
Revises: 0002_bbox_refine_columns
Create Date: 2026-05-15

The original column was declared ``String(16)``, which fits ``QUEUED``,
``RUNNING``, ``SUCCEEDED``, ``FAILED``, ``CANCELLED`` and
``REFINING_BBOXES`` (15 chars), but *not* ``PARTIAL_SUCCEEDED`` (17
chars). Any job that asked for ``stages.bbox_refine`` and reached the
``PARTIAL_SUCCEEDED`` transition therefore failed with
``StringDataRightTruncationError`` from asyncpg and retried until
``job_max_attempts`` was exhausted.

We widen the column to 24 to fit the longest current value plus
headroom for future statuses. The same widening applies to
``bbox_refine_status`` (currently 16) for symmetry, even though its
longest value (``succeeded``) already fits.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_widen_job_status"
down_revision = "0002_bbox_refine_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "extraction_jobs",
        "status",
        existing_type=sa.String(length=16),
        type_=sa.String(length=24),
        existing_nullable=False,
    )
    op.alter_column(
        "extraction_jobs",
        "bbox_refine_status",
        existing_type=sa.String(length=16),
        type_=sa.String(length=24),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "extraction_jobs",
        "bbox_refine_status",
        existing_type=sa.String(length=24),
        type_=sa.String(length=16),
        existing_nullable=True,
    )
    op.alter_column(
        "extraction_jobs",
        "status",
        existing_type=sa.String(length=24),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
