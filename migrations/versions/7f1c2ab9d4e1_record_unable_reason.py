"""record why a run produced no patch

A finished run with an empty diff used to be indistinguishable from one whose
adapter died quietly: the reason lived only in the adapter's return value, and the
row kept silence. The bench needs that reason to tell an honest agent failure from
an infrastructure one.

Revision ID: 7f1c2ab9d4e1
Revises: 26d26478baf4
Create Date: 2026-11-14 01:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7f1c2ab9d4e1"
down_revision: str | Sequence[str] | None = "26d26478baf4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("unable_reason", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "unable_reason")
