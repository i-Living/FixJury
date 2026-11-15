"""judge configurations, assessments and escalations

The machine verdict from `0004_evaluation_core` says what was decided; these three tables
say who decided it and why another opinion was asked. An assessment keeps the raw reply,
because a normalised row cannot hold what the model actually said and a calibration run
needs the original.

No new enum type: an assessment's verdict reuses `evaluation_verdict`, so the two can never
drift apart.

Revision ID: 0005_judge_assessments
Revises: 0004_evaluation_core
Create Date: 2026-11-15 07:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_judge_assessments"
down_revision: str | Sequence[str] | None = "0004_evaluation_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "judge_configurations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("primary_model", sa.String(length=128), nullable=False),
        sa.Column("escalation_model", sa.String(length=128), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_judge_configurations_hash", "judge_configurations", ["config_hash"], unique=True
    )

    op.create_table(
        "judge_assessments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("evaluation_run_id", sa.UUID(), nullable=False),
        sa.Column("judge_configuration_id", sa.UUID(), nullable=False),
        sa.Column("judge_id", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column(
            "verdict",
            postgresql.ENUM("ACCEPTED", "REJECTED", "INCONCLUSIVE", name="evaluation_verdict", create_type=False),
            nullable=False,
        ),
        sa.Column("overall_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column("escalated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tokens_in", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_out", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "cost_list_usd",
            sa.Numeric(precision=10, scale=4),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("latency_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["evaluation_run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["judge_configuration_id"], ["judge_configurations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_judge_assessments_evaluation", "judge_assessments", ["evaluation_run_id"], unique=False
    )

    op.create_table(
        "judge_escalations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("evaluation_run_id", sa.UUID(), nullable=False),
        sa.Column("round_no", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("escalated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "triggers",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["evaluation_run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_judge_escalations_evaluation", "judge_escalations", ["evaluation_run_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_judge_escalations_evaluation", table_name="judge_escalations")
    op.drop_table("judge_escalations")
    op.drop_index("ix_judge_assessments_evaluation", table_name="judge_assessments")
    op.drop_table("judge_assessments")
    op.drop_index("ix_judge_configurations_hash", table_name="judge_configurations")
    op.drop_table("judge_configurations")
    # No enum type is created here: the verdict reuses `evaluation_verdict`, which
    # revision `0004_evaluation_core` owns and drops.
