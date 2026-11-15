"""an evaluation that failed says why

`evaluation_runs.status` already distinguishes `failed` from `finished`, but a failed row
carried nothing that said what went wrong: the reason lived in a log line and a benchmark
report could not tell "the provider timed out" from "the run had no check evidence". A
benchmark that judges every pair needs that distinction in the database, because a judge
failure must never be read as a verdict about a patch.

Nullable, additive, and no historical row is touched: an evaluation written before this
revision was finished, so it has no error to record. `scoring_version` keeps its value and
stays NOT NULL — a failed evaluation stores the literal `unscored`, which is what it was.

Revision ID: 0006_evaluation_error
Revises: 0005_judge_assessments
Create Date: 2026-11-15 12:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_evaluation_error"
down_revision: str | Sequence[str] | None = "0005_judge_assessments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evaluation_runs", sa.Column("error", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("evaluation_runs", "error")
