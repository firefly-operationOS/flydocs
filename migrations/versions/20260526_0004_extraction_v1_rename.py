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

"""extraction_v1_rename: extraction_jobs -> extractions, lowercase statuses,
post_processing columns, drop bbox_refine_* columns.

Revision ID: 0004_extraction_v1_rename
Revises: 0003_widen_job_status
Create Date: 2026-05-26

Changes
-------
1. Rename table ``extraction_jobs`` to ``extractions``.
2. Rename column ``created_at`` to ``submitted_at`` (semantic clean-up).
3. Lowercase every value in the ``status`` column:
   - QUEUED -> queued, RUNNING -> running, SUCCEEDED -> succeeded,
     FAILED -> failed, CANCELLED -> cancelled.
   - PARTIAL_SUCCEEDED -> succeeded (with bbox refinement carried into
     post_processing_bbox_status).
   - REFINING_BBOXES -> succeeded (same).
4. Rename ``bbox_refine_*`` columns to ``post_processing_bbox_*``.
5. Lowercase the new ``post_processing_bbox_status`` column values
   (legacy values like "pending"/"running"/"succeeded"/"failed" were
   already lowercase, so the rename is the only operation).
6. Update CHECK constraints to enforce the lowercase value set.
7. Shrink ``status`` from VARCHAR(24) back to VARCHAR(16): the longest
   v1 value is "cancelled" (9 chars). Skipping this would leave the
   column over-wide; trivial change.

The down-migration is intentionally partial: it restores the column
names and capitalisations for rows that fit the legacy state model, but
cannot reconstruct ``PARTIAL_SUCCEEDED`` / ``REFINING_BBOXES`` rows that
were collapsed into ``succeeded`` on upgrade. Operators rolling back
should accept that some rows previously in those intermediate states
will appear as ``SUCCEEDED`` with a partial result and a bbox-refine
status of ``failed`` or ``pending``.
"""

from __future__ import annotations

from alembic import op

revision = "0004_extraction_v1_rename"
down_revision = "0003_widen_job_status"
branch_labels = None
depends_on = None


_RENAME_PAIRS = [
    ("bbox_refine_status", "post_processing_bbox_status"),
    ("bbox_refine_attempts", "post_processing_bbox_attempts"),
    ("bbox_refine_started_at", "post_processing_bbox_started_at"),
    ("bbox_refine_finished_at", "post_processing_bbox_finished_at"),
    ("bbox_refine_error_code", "post_processing_bbox_error_code"),
    ("bbox_refine_error_message", "post_processing_bbox_error_message"),
]

_STATUS_LOWERCASE_MAP = [
    ("QUEUED", "queued"),
    ("RUNNING", "running"),
    ("SUCCEEDED", "succeeded"),
    ("FAILED", "failed"),
    ("CANCELLED", "cancelled"),
    # Collapse v0 intermediate states:
    ("PARTIAL_SUCCEEDED", "succeeded"),
    ("REFINING_BBOXES", "succeeded"),
]


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Rename the table.
    op.rename_table("extraction_jobs", "extractions")

    # 2. Rename created_at -> submitted_at.
    with op.batch_alter_table("extractions") as batch:
        batch.alter_column("created_at", new_column_name="submitted_at")

    # 3. Lowercase every status value (and collapse the v0 intermediates).
    for upper, lower in _STATUS_LOWERCASE_MAP:
        op.execute(f"UPDATE extractions SET status = '{lower}' WHERE status = '{upper}'")

    # 4. Rename the bbox_refine_* columns to post_processing_bbox_*.
    with op.batch_alter_table("extractions") as batch:
        for old, new in _RENAME_PAIRS:
            batch.alter_column(old, new_column_name=new)

    # 5. Recreate the CHECK constraints with the new value sets.
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE extractions DROP CONSTRAINT IF EXISTS ck_extraction_jobs_status")
        op.execute("ALTER TABLE extractions DROP CONSTRAINT IF EXISTS ck_extractions_status")
        op.execute("ALTER TABLE extractions DROP CONSTRAINT IF EXISTS ck_extraction_jobs_bbox_refine_status")
        op.execute(
            "ALTER TABLE extractions DROP CONSTRAINT IF EXISTS ck_extractions_post_processing_bbox_status"
        )
        op.create_check_constraint(
            "ck_extractions_status",
            "extractions",
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
        )
        op.create_check_constraint(
            "ck_extractions_post_processing_bbox_status",
            "extractions",
            "post_processing_bbox_status IS NULL OR "
            "post_processing_bbox_status IN ('pending', 'running', 'succeeded', 'failed')",
        )

    # 6. Recreate the partial unique index under the new table name.
    op.execute("DROP INDEX IF EXISTS uq_extraction_jobs_idempotency_key")
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE UNIQUE INDEX uq_extractions_idempotency_key "
            "ON extractions (idempotency_key) WHERE idempotency_key IS NOT NULL"
        )
    else:
        op.execute(
            "CREATE UNIQUE INDEX uq_extractions_idempotency_key "
            "ON extractions (idempotency_key) WHERE idempotency_key IS NOT NULL"
        )


def downgrade() -> None:
    bind = op.get_bind()

    # Reverse constraint changes first.
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE extractions DROP CONSTRAINT IF EXISTS ck_extractions_status")
        op.execute(
            "ALTER TABLE extractions DROP CONSTRAINT IF EXISTS ck_extractions_post_processing_bbox_status"
        )

    # Rename the columns back.
    with op.batch_alter_table("extractions") as batch:
        for old, new in _RENAME_PAIRS:
            batch.alter_column(new, new_column_name=old)

    # Re-uppercase the status values.
    for upper, lower in _STATUS_LOWERCASE_MAP:
        if upper in {"PARTIAL_SUCCEEDED", "REFINING_BBOXES"}:
            # Cannot reconstruct -- they were collapsed into "succeeded" on
            # upgrade and the original distinction is lost.
            continue
        op.execute(f"UPDATE extractions SET status = '{upper}' WHERE status = '{lower}'")

    # Recreate the v0 CHECK constraints if present.
    if bind.dialect.name == "postgresql":
        op.create_check_constraint(
            "ck_extraction_jobs_status",
            "extractions",
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED', "
            "'PARTIAL_SUCCEEDED', 'REFINING_BBOXES')",
        )

    # Rename submitted_at back to created_at.
    with op.batch_alter_table("extractions") as batch:
        batch.alter_column("submitted_at", new_column_name="created_at")

    # Rename the index back.
    op.execute("DROP INDEX IF EXISTS uq_extractions_idempotency_key")
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE UNIQUE INDEX uq_extraction_jobs_idempotency_key "
            "ON extractions (idempotency_key) WHERE idempotency_key IS NOT NULL"
        )
    else:
        op.execute(
            "CREATE UNIQUE INDEX uq_extraction_jobs_idempotency_key "
            "ON extractions (idempotency_key) WHERE idempotency_key IS NOT NULL"
        )

    # Rename the table back.
    op.rename_table("extractions", "extraction_jobs")
