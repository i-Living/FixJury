"""Database schema for FixJury.

Enum member names equal their stored values on purpose: SQLAlchemy persists
member *names* by default, so ``RunStatus.infra_failed`` lands in PostgreSQL as
``infra_failed`` without a custom ``values_callable``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Difficulty(StrEnum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class CutoffAssumption(StrEnum):
    clean = "clean"
    unknown = "unknown"


class CostBasis(StrEnum):
    list_price = "list_price"
    amortized = "amortized"


class RunStatus(StrEnum):
    queued = "queued"
    running = "running"
    finished = "finished"
    infra_failed = "infra_failed"
    cancelled = "cancelled"


class ReviewerVerdict(StrEnum):
    accept = "accept"
    reject = "reject"
    abstain = "abstain"


class HumanVerdict(StrEnum):
    accept = "accept"
    reject = "reject"


class HumanResolution(StrEnum):
    kept = "kept"
    changed = "changed"


class RejectionSource(StrEnum):
    automated = "automated"
    reviewer = "reviewer"
    human = "human"


class RejectionCode(StrEnum):
    WRONG_ROOT_CAUSE = "WRONG_ROOT_CAUSE"
    INCOMPLETE_FIX = "INCOMPLETE_FIX"
    EXTRA_FILES = "EXTRA_FILES"
    NO_REGRESSION_TEST = "NO_REGRESSION_TEST"
    API_BREAK = "API_BREAK"
    SPEC_VIOLATION = "SPEC_VIOLATION"
    TEST_OVERFIT = "TEST_OVERFIT"
    BUILD_BREAK = "BUILD_BREAK"
    UNREADABLE_DIFF = "UNREADABLE_DIFF"
    TIMEOUT_LIMIT = "TIMEOUT_LIMIT"


# A run can only be read as an exclusion if the reason fits the column it is stored
# in: an adapter builds it from a git message, and PostgreSQL rejects the overflow.
UNABLE_REASON_LENGTH = 120


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    repo_url: Mapped[str] = mapped_column(String(512))
    license: Mapped[str] = mapped_column(String(64))
    base_commit: Mapped[str] = mapped_column(String(40))
    fix_commit: Mapped[str | None] = mapped_column(String(40))
    fix_merged_at: Mapped[date | None] = mapped_column(Date)
    issue_number: Mapped[int] = mapped_column(Integer)
    issue_url: Mapped[str] = mapped_column(String(512))
    issue_created_at: Mapped[date] = mapped_column(Date)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    difficulty: Mapped[Difficulty] = mapped_column(SAEnum(Difficulty, name="difficulty"))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    cutoff_assumption: Mapped[CutoffAssumption] = mapped_column(
        SAEnum(CutoffAssumption, name="cutoff_assumption")
    )
    heldout_ref: Mapped[str] = mapped_column(String(256))
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    frozen_by: Mapped[str] = mapped_column(String(128))


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("task_id", "agent", "attempt", name="uq_runs_task_agent_attempt"),
        Index("ix_runs_agent_status", "agent", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    agent: Mapped[str] = mapped_column(String(64))
    agent_version: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    attempt: Mapped[int] = mapped_column(SmallInteger, default=1)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus, name="run_status"), default=RunStatus.queued
    )
    declared_success: Mapped[bool | None]
    # Why the adapter could not produce a patch. Without it an empty diff is
    # indistinguishable from a crashed harness.
    unable_reason: Mapped[str | None] = mapped_column(String(UNABLE_REASON_LENGTH))
    diff: Mapped[str | None] = mapped_column(Text)
    diff_sha256: Mapped[str | None] = mapped_column(String(64))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    sandbox_seconds: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    cost_list_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal("0"))
    pricing_version: Mapped[str] = mapped_column(String(32))
    config_hash: Mapped[str] = mapped_column(String(64))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Verdict(Base):
    __tablename__ = "verdicts"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    tests_pass: Mapped[bool] = mapped_column(default=False)
    lint_pass: Mapped[bool] = mapped_column(default=False)
    type_pass: Mapped[bool] = mapped_column(default=False)
    policy_pass: Mapped[bool] = mapped_column(default=False)
    heldout_pass: Mapped[bool | None]
    automated_pass: Mapped[bool] = mapped_column(default=False)
    tool_versions: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    reviewer_verdict: Mapped[ReviewerVerdict | None] = mapped_column(
        SAEnum(ReviewerVerdict, name="reviewer_verdict")
    )
    reviewer_reasons: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    reviewer_model: Mapped[str | None] = mapped_column(String(128))
    human_verdict: Mapped[HumanVerdict | None] = mapped_column(
        SAEnum(HumanVerdict, name="human_verdict")
    )
    human_reasons: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    human_resolution: Mapped[HumanResolution | None] = mapped_column(
        SAEnum(HumanResolution, name="human_resolution")
    )
    human_reviewer: Mapped[str | None] = mapped_column(String(128))
    review_seconds: Mapped[int | None] = mapped_column(Integer)
    blind: Mapped[bool] = mapped_column(default=True)
    batch_id: Mapped[str | None] = mapped_column(String(64))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Rejection(Base):
    __tablename__ = "rejections"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[RejectionSource] = mapped_column(
        SAEnum(RejectionSource, name="rejection_source")
    )
    code: Mapped[RejectionCode] = mapped_column(SAEnum(RejectionCode, name="rejection_code"))
    note: Mapped[str | None] = mapped_column(Text)


class ReviewBatch(Base):
    __tablename__ = "review_batches"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    run_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    order_seed: Mapped[int] = mapped_column(BigInteger)
    pass_no: Mapped[int] = mapped_column(SmallInteger, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewer: Mapped[str] = mapped_column(String(128))


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    agent: Mapped[str] = mapped_column(String(64))
    cost_basis: Mapped[CostBasis] = mapped_column(SAEnum(CostBasis, name="cost_basis"))
    config_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
