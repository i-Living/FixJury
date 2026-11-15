"""record what each check printed beside the verdict

Five booleans cannot be cited by a judge and cannot be rebuilt without re-running
verification. The column carries the command, the tool, the exit code and the output tail
of every check the verification container executed.

Old rows get an empty list: they were verified before this column existed, and an empty
list says exactly that instead of pretending the checks printed nothing.

Revision ID: 0003_verdict_evidence
Revises: 7f1c2ab9d4e1
Create Date: 2026-11-15 04:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_verdict_evidence"
down_revision: str | Sequence[str] | None = "7f1c2ab9d4e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "verdicts",
        sa.Column(
            "check_results",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("verdicts", "check_results")
