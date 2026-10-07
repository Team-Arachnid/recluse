"""Phase 8: ground truth counts per alert bucket

A deduplicated alert stands for every flow its bucket absorbed, but
``ground_truth_label`` records only the first. On a replay every unclassified
anomaly is attributed to one derived source host, so a bucket mixes Stage 2's
benign false positives with its real catches, and the first flow is nearly
always a false positive. ``ground_truth_counts`` tallies every label the bucket
absorbs. Demo-only and nullable: live capture has no labels, and rows stored
before this revision are left null rather than backfilled with a composition
nobody recorded.

Revision ID: 23464adcb561
Revises: b2452313f8f5
Create Date: 2026-10-07 01:51:56.672385
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "23464adcb561"
down_revision: str | None = "b2452313f8f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ground_truth_counts", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.drop_column("ground_truth_counts")
