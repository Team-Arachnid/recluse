"""Phase 7: drift snapshots, flow samples and retrain runs

Four tables, closing the loop from traffic back to the model.

``flow_samples`` is the one worth a second look. Drift has to be measured over
scored traffic, and ``alerts`` holds only the rows that crossed a threshold -- a
PSI computed from those answers "do the alerts look unusual" rather than "has the
traffic moved". So the pipeline keeps a systematic sample of every flow it
scores, bounded and pruned.

``drift_runs`` plus ``drift_features`` store a nightly snapshot: one row per run,
one per feature per run. Per feature rather than a JSON blob on the run, because
the screen plots PSI per feature over time and that is a query by feature across
runs.

``retrain_runs`` exists from the moment the dashboard asks for a retrain, which
is what keeps the API out of the fitting business.

Revision ID: b2452313f8f5
Revises: 9a6857dcba76
Create Date: 2026-10-06 20:07:04.351414
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2452313f8f5"
down_revision: str | None = "9a6857dcba76"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "drift_runs",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("observed_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rows_observed", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(length=255), nullable=True),
        sa.Column("reference_rows", sa.Integer(), nullable=True),
        sa.Column("features_scored", sa.Integer(), nullable=False),
        sa.Column("max_psi", sa.Float(), nullable=False),
        sa.Column("moderate_count", sa.Integer(), nullable=False),
        sa.Column("significant_count", sa.Integer(), nullable=False),
        sa.Column("retrain_recommended", sa.Boolean(), nullable=False),
        sa.Column("score_histogram", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_drift_runs")),
    )
    with op.batch_alter_table("drift_runs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_drift_runs_computed_at"), ["computed_at"], unique=False
        )

    op.create_table(
        "flow_samples",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("anomaly_score", sa.Float(), nullable=True),
        sa.Column("alerted", sa.Boolean(), nullable=False),
        sa.Column("raw_flow", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('replay', 'live', 'api')",
            name=op.f("ck_flow_samples_flow_sample_source_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_flow_samples")),
    )
    with op.batch_alter_table("flow_samples", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_flow_samples_scored_at"), ["scored_at"], unique=False)
        batch_op.create_index("ix_flow_samples_scored_at_id", ["scored_at", "id"], unique=False)

    op.create_table(
        "retrain_runs",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_by", sa.String(length=128), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("labels_consumed", sa.Integer(), nullable=False),
        sa.Column("false_positives_consumed", sa.Integer(), nullable=False),
        sa.Column("true_positives_consumed", sa.Integer(), nullable=False),
        sa.Column("champion_version", sa.String(length=64), nullable=True),
        sa.Column("challenger_version", sa.String(length=64), nullable=True),
        sa.Column("champion_pr_auc", sa.Float(), nullable=True),
        sa.Column("challenger_pr_auc", sa.Float(), nullable=True),
        sa.Column("held_out_split", sa.String(length=64), nullable=True),
        sa.Column("promoted", sa.Boolean(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=True),
        sa.Column("comparison", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('requested', 'running', 'completed', 'failed', 'cancelled')",
            name=op.f("ck_retrain_runs_retrain_status_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retrain_runs")),
    )
    with op.batch_alter_table("retrain_runs", schema=None) as batch_op:
        batch_op.create_index(
            "ix_retrain_runs_status_requested_at", ["status", "requested_at"], unique=False
        )

    op.create_table(
        "drift_features",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("run_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("feature", sa.String(length=128), nullable=False),
        sa.Column("psi", sa.Float(), nullable=False),
        sa.Column("band", sa.String(length=16), nullable=False),
        sa.Column("expected", sa.JSON(), nullable=True),
        sa.Column("actual", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "band IN ('stable', 'moderate', 'significant')",
            name=op.f("ck_drift_features_drift_band_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["drift_runs.id"],
            name=op.f("fk_drift_features_run_id_drift_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_drift_features")),
    )
    with op.batch_alter_table("drift_features", schema=None) as batch_op:
        batch_op.create_index("ix_drift_features_feature_run", ["feature", "run_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_drift_features_run_id"), ["run_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("drift_features", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_drift_features_run_id"))
        batch_op.drop_index("ix_drift_features_feature_run")

    op.drop_table("drift_features")
    with op.batch_alter_table("retrain_runs", schema=None) as batch_op:
        batch_op.drop_index("ix_retrain_runs_status_requested_at")

    op.drop_table("retrain_runs")
    with op.batch_alter_table("flow_samples", schema=None) as batch_op:
        batch_op.drop_index("ix_flow_samples_scored_at_id")
        batch_op.drop_index(batch_op.f("ix_flow_samples_scored_at"))

    op.drop_table("flow_samples")
    with op.batch_alter_table("drift_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_drift_runs_computed_at"))

    op.drop_table("drift_runs")
