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

"""Add bbox-refine leg columns to ``extraction_jobs``.

Revision ID: 0002_bbox_refine_columns
Revises: 0001_init
Create Date: 2026-05-15

Adds the per-job state for the out-of-band bbox refinement worker:

* ``bbox_refine_status``       -- ``pending`` / ``running`` / ``succeeded``
                                  / ``failed`` / ``null``.
* ``bbox_refine_attempts``     -- retry counter for the refine leg,
                                  independent of the main extraction's
                                  ``attempts``.
* ``bbox_refine_started_at``   -- timestamp the refine worker first
                                  transitioned the leg to ``running``.
* ``bbox_refine_finished_at``  -- timestamp the leg reached a terminal
                                  sub-state.
* ``bbox_refine_error_code``   -- stable code if the refine leg failed
                                  permanently; main result still readable.
* ``bbox_refine_error_message`` -- free-form message paired with the code.

All columns are nullable: jobs submitted without ``stages.bbox_refine``
never populate them and the default flow (``QUEUED -> RUNNING ->
SUCCEEDED``) is unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_bbox_refine_columns"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extraction_jobs",
        sa.Column("bbox_refine_status", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column(
            "bbox_refine_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column("bbox_refine_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column("bbox_refine_finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column("bbox_refine_error_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column("bbox_refine_error_message", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("extraction_jobs", "bbox_refine_error_message")
    op.drop_column("extraction_jobs", "bbox_refine_error_code")
    op.drop_column("extraction_jobs", "bbox_refine_finished_at")
    op.drop_column("extraction_jobs", "bbox_refine_started_at")
    op.drop_column("extraction_jobs", "bbox_refine_attempts")
    op.drop_column("extraction_jobs", "bbox_refine_status")
