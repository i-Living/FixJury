"""evaluation core: evaluation runs, results and findings

V1.1 keeps the V1 verdict row as what it is — the automated checks and the human review —
and adds the machine evaluation beside it: the verdict, the weighted score, the findings
with their evidence, and the configuration the whole thing ran under.

Additive only: no existing table changes shape, and no historical row is touched.

Revision ID: 0004_evaluation_core
Revises: 0003_verdict_evidence
Create Date: 2026-11-15 05:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_evaluation_core"
down_revision: str | Sequence[str] | None = "0003_verdict_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Labels in the order the models declare them: the verdict is spelled as SPEC §4.5 does.
EVALUATION_VERDICT_LABELS = ("ACCEPTED", "REJECTED", "INCONCLUSIVE")

CRITERION_LABELS = (
    "correctness",
    "regression_safety",
    "code_quality",
    "maintainability",
    "security",
    "minimality",
    "requirement_compliance",
)


def upgrade() -> None:
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "source",
            sa.Enum("benchmark", "review", name="evaluation_source"),
            nullable=False,
        ),
        sa.Column("subject_ref", sa.String(length=256), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=True),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("scoring_profile", sa.String(length=64), nullable=False),
        sa.Column("scoring_version", sa.String(length=32), nullable=False),
        sa.Column("judge_config_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum("running", "finished", "failed", name="evaluation_status"),
            nullable=False,
        ),
        sa.Column("tokens_in", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tokens_out", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "cost_list_usd",
            sa.Numeric(precision=10, scale=4),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("pricing_version", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evaluation_runs_run", "evaluation_runs", ["run_id"], unique=False)
    op.create_index(
        "ix_evaluation_runs_subject",
        "evaluation_runs",
        ["source", "subject_ref", "created_at"],
        unique=False,
    )

    op.create_table(
        "evaluation_results",
        sa.Column("evaluation_run_id", sa.UUID(), nullable=False),
        sa.Column(
            "verdict",
            sa.Enum(*EVALUATION_VERDICT_LABELS, name="evaluation_verdict"),
            nullable=False,
        ),
        # The judge's own verdict before the critical-failure rules; the type already
        # exists from the column above, so it must not be created a second time.
        sa.Column(
            "judge_verdict",
            postgresql.ENUM(*EVALUATION_VERDICT_LABELS, name="evaluation_verdict", create_type=False),
            nullable=True,
        ),
        sa.Column("overall_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column(
            "criteria",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("reasoning_summary", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "limitations",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("escalated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["evaluation_run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("evaluation_run_id"),
    )

    op.create_table(
        "evaluation_findings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("evaluation_run_id", sa.UUID(), nullable=False),
        sa.Column(
            "severity",
            sa.Enum("info", "minor", "major", "critical", name="finding_severity"),
            nullable=False,
        ),
        sa.Column("category", sa.Enum(*CRITERION_LABELS, name="criterion"), nullable=False),
        sa.Column("source", sa.Enum("automated", "judge", name="finding_source"), nullable=False),
        sa.Column("confirmed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("evidence_ref", sa.String(length=256), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=True),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("impact", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["evaluation_run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evaluation_findings_run_severity",
        "evaluation_findings",
        ["evaluation_run_id", "severity"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_evaluation_findings_run_severity", table_name="evaluation_findings")
    op.drop_table("evaluation_findings")
    op.drop_table("evaluation_results")
    op.drop_index("ix_evaluation_runs_subject", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_run", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")

    # PostgreSQL keeps enum types after their tables are gone, so a second upgrade fails
    # with "type ... already exists".
    for enum_name in (
        "criterion",
        "evaluation_source",
        "evaluation_status",
        "evaluation_verdict",
        "finding_severity",
        "finding_source",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
