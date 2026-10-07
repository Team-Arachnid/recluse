"""Phase 9: shadow scores, the live burn-in

One row per live flow scored in shadow mode: its Stage 1 confidence, its
Stage 2 reconstruction error on every flow (not only where the cascade
consulted Stage 2), what the dataset thresholds would have raised, and the
observed endpoints. A local tau_anom is cut from these before any live alert is
allowed into the queue.

Revision ID: 930ac6f6f5c7
Revises: 23464adcb561
Create Date: 2026-10-07 02:46:26.697179
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "930ac6f6f5c7"
down_revision: str | None = "23464adcb561"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shadow_scores",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("capture_source", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("src_ip", sa.String(length=64), nullable=False),
        sa.Column("src_port", sa.Integer(), nullable=False),
        sa.Column("dst_ip", sa.String(length=64), nullable=False),
        sa.Column("dst_port", sa.Integer(), nullable=False),
        sa.Column("protocol", sa.String(length=8), nullable=False),
        sa.Column("stage1_confidence", sa.Float(), nullable=True),
        sa.Column("stage2_error", sa.Float(), nullable=False),
        sa.Column("would_alert", sa.String(length=32), nullable=True),
        sa.CheckConstraint(
            "would_alert IS NULL OR would_alert IN ('KNOWN', 'UNCLASSIFIED_ANOMALY')",
            name=op.f("ck_shadow_scores_shadow_would_alert_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shadow_scores")),
    )
    with op.batch_alter_table("shadow_scores", schema=None) as batch_op:
        batch_op.create_index(
            "ix_shadow_scores_session_captured_at", ["session_id", "captured_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("shadow_scores", schema=None) as batch_op:
        batch_op.drop_index("ix_shadow_scores_session_captured_at")

    op.drop_table("shadow_scores")
