# Phase 0 — Foundation: план реализации

> Для Hermes: выполнять задачами по порядку, по одному коммиту на задачу. `--no-verify` запрещён; хук упал — показать ошибку и спросить. Спека и решения — [`../PLAN.md`](../PLAN.md), правила репозитория — [`../../AGENTS.md`](../../AGENTS.md).

**Goal:** рабочий каркас FixJury — проект на uv, линт/типы/тесты, локальная инфраструктура Postgres+Redis, шесть таблиц через Alembic, CI на GitHub Actions.

**Architecture:** src-layout (`src/fixjury/`), конфиг через `pydantic-settings`, SQLAlchemy 2.0 async + Alembic, инфраструктура в `docker-compose.yml`, качество — `ruff` + `mypy --strict` + `pytest`.

**Tech stack:** Python 3.12, uv, FastAPI, SQLAlchemy 2.0, asyncpg, Alembic, pydantic v2, PostgreSQL 16, Redis 7, GitHub Actions.

## Предусловия

- `uv --version` отвечает. На этом хосте uv лежит в `~/.hermes/bin/uv` и не в PATH: `export PATH="$HOME/.hermes/bin:$PATH"` (или поставить отдельно).
- Docker работает: `docker info` → Server Version 29.x (проверено на этом хосте).
- Системный Python 3.13 не используется: `.python-version` фиксирует 3.12, uv скачает интерпретатор сам.
- Репозиторий: ветка `master`, три коммита (concept note, spec/plan, license), рабочее дерево чистое.

---

## Task 0.1 — Каркас проекта

**Objective:** проект ставится и запускается: `uv sync` + `uv run fixjury version` → `0.1.0`.

**Files:**
- Create: `.python-version`
- Create: `pyproject.toml`
- Create: `src/fixjury/__init__.py`
- Create: `src/fixjury/cli.py`
- Create: `.gitignore`
- Create: `.env.example`

**Step 1: зафиксировать интерпретатор**

`.python-version`:
```
3.12
```

**Step 2: описать проект**

`pyproject.toml`:
```toml
[project]
name = "fixjury"
version = "0.1.0"
description = "Agent-agnostic reliability layer for autonomous code repair"
readme = "README.md"
requires-python = ">=3.12"
license = { text = "MIT" }
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "pyyaml>=6.0",
    "docker>=7.1",
    "redis>=5.2",
    "typer>=0.15",
    "httpx>=0.28",
    "jinja2>=3.1",
    "matplotlib>=3.9",
    "opentelemetry-sdk>=1.29",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "ruff>=0.8",
    "mypy>=1.13",
    "pre-commit>=4.0",
    "types-pyyaml>=6.0",
]

[project.scripts]
fixjury = "fixjury.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/fixjury"]
```

**Step 3: пакет и CLI**

`src/fixjury/__init__.py`:
```python
"""FixJury — agent-agnostic reliability layer for autonomous code repair."""

__version__ = "0.1.0"
```

`src/fixjury/cli.py`:
```python
from __future__ import annotations

import typer

from fixjury import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """FixJury — agent-agnostic reliability layer for autonomous code repair."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)
```

**Step 4: игнор и пример окружения**

`.gitignore`:
```
.venv/
__pycache__/
*.py[co]
.ruff_cache/
.mypy_cache/
.pytest_cache/
.env
runs/
dist/
build/
*.egg-info/
web/node_modules/
web/.next/
```

`.env.example`:
```
# Ports are published by docker-compose.yml on 127.0.0.1 only.
FIXJURY_DATABASE_URL=postgresql+asyncpg://fixjury:fixjury@127.0.0.1:55432/fixjury
FIXJURY_REDIS_URL=redis://127.0.0.1:16379/0
FIXJURY_LOG_LEVEL=INFO
```

**Step 5: установить**

Run:
```bash
cd /home/ls/dev/ai-repair-reliability-platform
uv sync
```
Expected: созданы `.venv/` и `uv.lock`, exit 0.

**Step 6: проверить запуск**

Run: `uv run fixjury version`
Expected: `0.1.0`

**Step 7: commit**

```bash
git add .python-version pyproject.toml uv.lock src/fixjury/__init__.py src/fixjury/cli.py .gitignore .env.example
git commit -m "chore: bootstrap project skeleton"
```

---

## Task 0.2 — Quality gates

**Objective:** `ruff`, `mypy --strict` и `pytest` настроены; линт и типы зелёные на каркасе. Тесты добавляются в 0.4, когда появится что проверять.

**Files:**
- Modify: `pyproject.toml` (append tool sections)

**Step 1: дописать конфиги в конец `pyproject.toml`**

```toml
[tool.ruff]
line-length = 100
target-version = "py312"
extend-exclude = ["migrations/versions"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "ASYNC", "RUF", "S"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101"]

[tool.ruff.format]
quote-style = "double"

[tool.mypy]
python_version = "3.12"
strict = true
files = ["src", "tests"]
mypy_path = "src"
plugins = ["pydantic.mypy"]

[[tool.mypy.overrides]]
module = ["docker.*", "matplotlib.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_default_fixture_loop_scope = "function"
markers = ["integration: requires docker or postgres"]
```

**Step 2: проверить линт и типы**

Run:
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src
```
Expected: `All checks passed!`, `Success: no issues found in 2 source files`.

Если `ruff format --check` ругается — `uv run ruff format .` и перепроверить.

**Step 3: commit**

```bash
git add pyproject.toml
git commit -m "chore: add lint, type and test configuration"
```

---

## Task 0.3 — Postgres и Redis

**Objective:** `docker compose up -d` поднимает оба сервиса healthy, порты не конфликтуют с другими проектами хоста.

**Files:**
- Create: `docker-compose.yml`

**Step 1: compose**

`docker-compose.yml`:
```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: fixjury
      POSTGRES_PASSWORD: fixjury
      POSTGRES_DB: fixjury
    ports:
      - "127.0.0.1:55432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U fixjury -d fixjury"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--save", "", "--appendonly", "no"]
    ports:
      - "127.0.0.1:16379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  pgdata:
```

Порты 55432/16379, а не 5432/6379: на хосте уже работают другие Postgres и Redis, конфликт портов даёт непонятную ошибку на старте.

**Step 2: поднять**

Run:
```bash
docker compose up -d && sleep 5 && docker compose ps --format "table {{.Service}}\t{{.Status}}"
```
Expected: две строки, обе со `(healthy)`.

**Step 3: commit**

```bash
git add docker-compose.yml
git commit -m "chore: add local postgres and redis"
```

---

## Task 0.4 — Конфиг, модели, Alembic, smoke-тесты

**Objective:** в базе появляются 6 таблиц приложения + `alembic_version`, схема пересоздаётся с нуля одной командой, 4 smoke-теста зелёные.

**Files:**
- Create: `src/fixjury/config.py`
- Create: `src/fixjury/models.py`
- Create: `tests/unit/test_smoke.py`
- Create: `migrations/env.py` (перезапись сгенерированного файла)
- Generated: `alembic.ini`, `migrations/versions/<rev>_initial_schema.py`

**Step 1: конфиг**

`src/fixjury/config.py`:
```python
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Every field is overridable as FIXJURY_<FIELD>."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="FIXJURY_", extra="ignore")

    database_url: str = "postgresql+asyncpg://fixjury:fixjury@127.0.0.1:55432/fixjury"
    redis_url: str = "redis://127.0.0.1:16379/0"
    log_level: str = "INFO"

    sandbox_image: str = "python:3.12-slim"
    sandbox_cpu: float = 2.0
    sandbox_memory_mb: int = 2048
    sandbox_timeout_s: int = 1200

    pricing_file: str = "config/pricing.yaml"


settings = Settings()
```

**Step 2: модели**

`src/fixjury/models.py` (файл целиком):
```python
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
```

**Step 3: smoke-тесты**

`tests/unit/test_smoke.py`:
```python
from fixjury.config import Settings
from fixjury.models import Base, RejectionCode, RunStatus


def test_settings_reads_explicit_value() -> None:
    url = "postgresql+asyncpg://user:pass@127.0.0.1:55432/fixjury"
    assert Settings(database_url=url).database_url == url


def test_settings_use_env_prefix() -> None:
    assert Settings.model_config["env_prefix"] == "FIXJURY_"


def test_models_declare_six_tables() -> None:
    assert set(Base.metadata.tables) == {
        "tasks",
        "runs",
        "verdicts",
        "rejections",
        "review_batches",
        "metric_snapshots",
    }


def test_enum_values_match_codes() -> None:
    assert RunStatus.infra_failed.value == "infra_failed"
    assert RejectionCode.EXTRA_FILES.value == "EXTRA_FILES"
```

**Step 4: инициализировать Alembic**

Run:
```bash
uv run alembic init migrations
```
Expected: созданы `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/`.

**Step 5: заменить `migrations/env.py` целиком**

```python
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from fixjury.config import settings
from fixjury.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

**Step 6: сгенерировать миграцию**

Run:
```bash
uv run alembic revision --autogenerate -m "initial schema"
```
Expected: `Generating .../migrations/versions/<rev>_initial_schema.py ... done`.

**Step 7: дописать удаление enum-типов в `downgrade()`**

PostgreSQL не удаляет enum-типы вместе с таблицами. Без этого шага `alembic downgrade base && alembic upgrade head` падает на `type "run_status" already exists` — проверено на живой базе.

В конец функции `downgrade()` в `migrations/versions/<rev>_initial_schema.py`, после `# ### end Alembic commands ###`, добавить:

```python
    # PostgreSQL keeps enum types after their tables are gone, so a second
    # `alembic upgrade head` fails with "type ... already exists".
    for enum_name in (
        "cost_basis",
        "cutoff_assumption",
        "difficulty",
        "human_resolution",
        "human_verdict",
        "rejection_code",
        "rejection_source",
        "reviewer_verdict",
        "run_status",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
```

**Step 8: применить и проверить схему**

Run:
```bash
uv run alembic upgrade head
docker compose exec -T postgres psql -U fixjury -d fixjury -c '\dt'
```
Expected: `Running upgrade  -> <rev>, initial schema`; 7 таблиц — `alembic_version`, `metric_snapshots`, `rejections`, `review_batches`, `runs`, `tasks`, `verdicts`.

**Step 9: прогнать полную цепочку**

Run:
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q
```
Expected: `All checks passed!`, `Success: no issues found in 4 source files`, `4 passed`.

**Step 10: пересоздать схему с нуля (проверка обратимости)**

Run:
```bash
uv run alembic downgrade base && uv run alembic upgrade head
docker compose exec -T postgres psql -U fixjury -d fixjury -c '\dt'
```
Expected: обе команды без ошибок, снова 7 таблиц.

**Step 11: commit**

```bash
git add alembic.ini migrations src/fixjury/config.py src/fixjury/models.py tests/unit/test_smoke.py
git commit -m "feat: add settings, schema, initial migration and smoke tests"
```

---

## Task 0.5 — CI

**Objective:** пуш в любую ветку и любой PR прогоняют те же четыре проверки, что локально.

**Files:**
- Create: `.github/workflows/ci.yml`

**Step 1: workflow**

```yaml
name: CI

on:
  push:
    branches: ["**"]
  pull_request:

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - run: uv python install 3.12
      - run: uv sync --frozen

      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy src
      - run: uv run pytest -q
```

Модели и smoke-тесты не требуют Postgres, поэтому сервис-контейнер в фазе 0 не поднимается. Интеграционные тесты появятся в фазе 2 и получат отдельный job.

**Step 2: проверить локально ровно те же команды**

Run:
```bash
uv sync --frozen && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q
```
Expected: всё зелёное, `4 passed`.

**Step 3: commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: run lint, types and tests on every push"
```

> Первый прогон в Actions проверяется после `git push`, который делается только по отдельной команде владельца.

---

## Task 0.6 — Pre-commit

**Objective:** локальный хук не даёт закоммитить неотформатированный код.

**Files:**
- Create: `.pre-commit-config.yaml`

**Step 1: конфиг**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.17.4
    hooks:
      - id: ruff
        args: ["--fix"]
      - id: ruff-format
```

Тег держим равным установленной версии ruff (`uv run ruff --version`); при обновлении — `uv run pre-commit autoupdate`.

**Step 2: поставить и прогнать**

Run:
```bash
uv run pre-commit install
uv run pre-commit run --all-files
```
Expected: `ruff` и `ruff-format` — `Passed`. Если тег уже неактуален — `uv run pre-commit autoupdate`.
Если хук падает — показать ошибку и спросить, не обходить `--no-verify`.

**Step 3: commit**

```bash
git add .pre-commit-config.yaml
git commit -m "chore: add pre-commit hooks"
```

---

## VERIFICATION CHECKPOINT (после фазы 0)

- [ ] `uv run fixjury version` → `0.1.0`
- [ ] `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q` → `4 passed`
- [ ] `docker compose ps` → postgres и redis `(healthy)`
- [ ] `docker compose exec -T postgres psql -U fixjury -d fixjury -c '\dt'` → 7 таблиц
- [ ] `uv run alembic downgrade base && uv run alembic upgrade head` → схема пересоздаётся без ошибок
- [ ] `git status --short` → пусто; 10 коммитов (4 документа + 6 задач)

---

## Решения уровня «мелочи», принятые в этой фазе

- Порты Postgres/Redis — `55432`/`16379` на `127.0.0.1`, чтобы не пересекаться с сервисами хоста.
- Имена членов enum равны значениям: SQLAlchemy хранит имена, значит в PostgreSQL попадают ровно `infra_failed`, `EXTRA_FILES` и т. д.
- PostgreSQL не удаляет enum-типы при `drop_table`, поэтому в `downgrade()` миграции добавлен явный `DROP TYPE`: без него обратный прогон ломается.
- Один `@app.callback()` в Typer обязателен: при единственной команде Typer делает её командой по умолчанию и `fixjury version` падает с «unexpected extra argument».
- `.env` не коммитится, `.env.example` коммитится; префикс переменных — `FIXJURY_`.
- `mypy` проверяет `src` и `tests`; `migrations/` исключён из проверки типов, но линтуется ruff.
- CI не поднимает Postgres в фазе 0: smoke-тесты не ходят в БД.

## Вне фазы 0

- CLI-команды `tasks`, `run`, `bench`, `report` (фаза 1+).
- FastAPI-приложение и роуты (фаза 6).
- `config/pricing.yaml` (фаза 4, вместе с подсчётом стоимости).
- Тесты, требующие БД, и интеграционный job в CI (фаза 2).
