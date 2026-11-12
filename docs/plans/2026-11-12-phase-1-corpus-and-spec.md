# Phase 1 — Corpus и RepairSpec: план реализации

> Для Hermes: выполнять задачами по порядку, по одному коммиту на задачу. `--no-verify` запрещён; хук упал — показать ошибку и спросить. Спека и решения — [`../PLAN.md`](../PLAN.md), правила репозитория — [`../../AGENTS.md`](../../AGENTS.md).
> Порядок выполнения ниже отличается от нумерации PLAN §11: сначала инструменты, потом данные. Номера задач оставлены плановыми, чтобы ссылки не разъезжались.

**Goal:** пустой каталог `benchmarks/` превращается в замороженный корпус из 30 задач с held-out тестами, а в CLI появляются `tasks validate`, `tasks freeze` и `tasks draft`.

**Architecture:** корпус — это данные в репозитории (`benchmarks/tasks/<id>/task.yaml`, `benchmarks/heldout/<id>/`, `benchmarks/index.json`), логика — в `src/fixjury/tasks/` (schema, loader, validate, freeze, heldout) и `src/fixjury/spec/` (render, generator). Проверка корпуса — чистая функция, которую вызывает и CLI, и CI.

**Tech stack:** pydantic v2, PyYAML, httpx (GitHub API и LLM), typer, pytest; локальная проверка задач — `uv` и одноразовые venv (контейнерный runner появится в фазе 2–3).

## Предусловия

- Фаза 0 смержена: `1b95192`, дерево чистое, 6 таблиц в БД, CI зелёный.
- `gh` авторизован (`gh auth status`), лимит API 5000 запросов/час.
- Docker с postgres и redis поднят (`docker compose ps`).
- GitHub-репозитории целевых задач доступны без сети из контейнера агента: на этом этапе сеть нужна только инструментам корпуса.

---

## Task 1.1 — Схема задачи, held-out bundle и валидатор

**Objective:** `fixjury tasks validate benchmarks` проверяет каждый `task.yaml`, bundle held-out тестов и `index.json`; корпус без обязательного ключа, с датой раньше 2025-06-01, без `benchmarks/heldout/<id>/` или с чеком, указывающим в `benchmarks/`, даёт понятную ошибку и код возврата 1.

**Files:**
- Create: `src/fixjury/tasks/schema.py` — `TaskDocument`, `TaskIssue`, `parse_check`, константы cut-off и лицензий
- Create: `src/fixjury/tasks/loader.py` — чтение/запись `task.yaml`, `CorpusIndex`
- Create: `src/fixjury/tasks/heldout.py` — `HeldoutSpec`, разбор патча, проверка bundle (вторая половина 1.5)
- Create: `src/fixjury/tasks/validate.py` — правила корпуса
- Create: `src/fixjury/tasks/cli.py` — `tasks validate`
- Create: `src/fixjury/spec/render.py` — канонический YAML
- Modify: `src/fixjury/cli.py` — подприложение `tasks`
- Create: `tests/unit/test_task_schema.py`, `test_task_loader.py`, `test_task_heldout.py`, `test_task_validate.py`

**Step 1: схема.** `task.yaml` из PLAN §8 плюс `frozen_at`/`frozen_by`; `issue.body` — снимок текста issue. Черновик (до заморозки) может не иметь `license`, `base_commit`, дат и `heldout_ref`; замороженный документ обязан иметь их все. Проверка синтаксиса — pydantic, проверка правил корпуса — `validate.py`.

**Step 2: whitelist чеков.** `pytest [путь ...]`, `ruff check <путь ...>`, `mypy <путь ...>`; флаги, абсолютные пути, `..` и любой шелл-синтаксис отклоняются. Проверка — `parse_check`, один и тот же код у валидатора и будущего runner'а.

**Step 3: held-out bundle.** `benchmarks/heldout/<id>/files/<путь в репозитории>` хранит post-fix копию каждого тестового файла, который тронул upstream-фикс; `heldout.yaml` перечисляет эти файлы и pytest-команду, `reference.patch` обязателен только если у задачи нет `fix_commit`. Верификация копирует overlay поверх своего checkout, поэтому правка агента в том же тестовом файле не меняет held-out прогон. Валидатор сверяет `heldout.yaml` с содержимым `files/`: каждый файл существует и непуст, команда не запускает файл, которого bundle не даёт.

**Step 4: правила корпуса.** Обязательные факты, даты ≥ 2025-06-01, `cutoff_assumption: clean`, непустые `acceptance`/`checks`/`allowed_paths`, `heldout_ref == benchmarks/heldout/<id>`, отсутствие путей в `benchmarks/` в чеках и allowed_paths, согласие `index.json` с каталогом (в обе стороны, сортировка, дата заморозки не раньше самой свежей задачи), уникальность id, совпадение имени каталога и id.

**Step 5: рендер.** Один и тот же YAML пишут `freeze`, `draft` и тесты: порядок ключей фиксирован, даты — `!!timestamp`, многострочный `issue.body` — литеральный блок.

**Step 6: проверить**

Run:
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q
uv run fixjury tasks validate benchmarks --min-tasks 0   # после 1.3 → 30/30 valid
```
Expected: 56+ тестов зелёные; на пустом корпусе валидатор говорит `no task.yaml files found` и выходит с кодом 1.

**Step 7: commit** — `feat: add task schema, held-out bundle loader and corpus validator`.

---

## Task 1.2 — Заморозка задачи

**Objective:** `fixjury tasks freeze --draft <file>` добирает факты из GitHub (лицензия, `base_commit`, `fix_commit`, даты, снимок issue), пишет канонический `benchmarks/tasks/<id>/task.yaml` с `frozen_at`/`frozen_by` и отказывается перезаписывать уже замороженный файл.

**Files:**
- Create: `src/fixjury/tasks/freeze.py`
- Modify: `src/fixjury/tasks/cli.py` — `tasks freeze`
- Modify: `src/fixjury/config.py` — `github_token`, `github_api_url`
- Create: `src/fixjury/github.py` — тонкий клиент GitHub REST на httpx
- Create: `tests/unit/test_task_freeze.py` (mock transport, без сети)

**Step 1: факты.** `GET /repos/{o}/{r}` → SPDX и default branch; `GET /repos/{o}/{r}/issues/{n}` → title/body/created_at; фикс — `--fix-pr` (merge commit) или `--fix-commit`, иначе последнее `closed`-событие с `commit_id` в timeline. `base_commit` = первый родитель фикса, то есть merge-base с основной веткой (PLAN §5, «Pitfalls»).

**Step 2: правила.** Лицензия вне allowlist — отказ. Issue создан раньше 2025-06-01, фикс смержен раньше 2025-06-01, issue не закрыт коммитом, коммит не влит в основную ветку — отказ с причиной. `frozen_at` в файле уже стоит — отказ «add a new task version».

**Step 3: проверить.** Юнит-тесты на mock-транспорте: успешная заморозка, отказ на GPL, отказ на дату, отказ на повторную заморозку, конфликт `--fix-pr` с timeline.

**Step 4: commit** — `feat: freeze tasks from GitHub issues`.

---

## Task 1.5 — Held-out проверка и CI-guard

**Objective:** есть команда, которая доказывает sanity-правило PLAN §5 для задачи: чек-команды проходят на `base_commit` (no-op baseline), held-out падает без фикса и проходит с фиксом; в CI корпус валидируется на каждом пуше.

**Files:**
- Create: `scripts/corpus_sanity.py` — QA-прогон задачи в одноразовом venv (`uv venv` + `uv pip install`), пишет `benchmarks/heldout/<id>/sanity.json`
- Modify: `.github/workflows/ci.yml` — `uv run fixjury tasks validate benchmarks --min-tasks 30`
- Create: `tests/unit/test_corpus_sanity.py` — разбор результата и правило «sanity обязателен для замороженной задачи»

**Step 1: почему не в `src/`.** Verdict'ы считает контейнерный runner фазы 3; здесь нужен воспроизводимый QA-прогон корпуса на хосте, поэтому скрипт, а не публичный API.

**Step 2: правила прогона.** Bare-клон репозитория в scratch (без `--filter=blob:none`: частичный клон ломает чтение blob'ов в рабочем клоне), checkout `base_commit`, venv через `uv`, установка проекта с тестовыми зависимостями, затем: (1) `checks` на базе → pass, (2) копирование `heldout/<id>/files/` поверх checkout, held-out → fail, (3) `git diff base fix` (или `reference.patch`), `checks` + held-out → pass. Любой другой исход — задача не входит в корпус, а причина печатается в отчёте.

**Step 3: CI-guard монтирования.** Полноценный guard («контейнер агента не получает монтирований из `benchmarks/`») появится вместе с песочницей в фазе 2; здесь CI проверяет то, что можно проверить сейчас: ни один `checks`/`allowed_paths` не указывает в `benchmarks/` (`tasks validate`), bundle не попадает в путь агента по построению (агент получает checkout целевого репо).

**Step 4: commit** — `feat: add corpus sanity runner and CI validation`.

---

## Task 1.3 — Корпус из 30 задач

**Objective:** 30 замороженных задач из Python-репозиториев с лицензиями MIT/Apache-2.0/BSD-3-Clause, у каждой — `task.yaml`, `heldout/` bundle с проходящим sanity и запись в `benchmarks/index.json`.

**Files:**
- Create: `benchmarks/index.json`
- Create: `benchmarks/tasks/T-001..T-030/task.yaml`
- Create: `benchmarks/heldout/T-001..T-030/{heldout.patch,heldout.yaml,sanity.json}`
- Create: `benchmarks/drafts/T-001..T-030.yaml` — черновики, из которых заморожены задачи (след человека в цикле)
- Create: `benchmarks/README.md` — как читать корпус и как добавить задачу

**Step 1: отбор.** Кандидат = merged PR (5–80 изменённых строк) с добавленным тестовым файлом, связанный с issue, созданным не раньше 2025-06-01; лицензия репозитория в allowlist. Отбор автоматизирован: GitHub GraphQL по списку репозиториев.

**Step 2: черновик.** `acceptance` — из текста issue и изменённых файлов; `allowed_paths` — файлы фикса (тестовые каталоги — целиком); `checks` — полный `pytest`, если suite репозитория проходит на базе быстро, иначе конкретные тестовые файлы; `difficulty` — по размеру и числу файлов.

**Step 3: заморозка и sanity.** `fixjury tasks freeze` → `scripts/corpus_sanity.py`. В корпус попадают только задачи с `sanity.json`, где все четыре исхода совпали с ожиданием. Провалившиеся кандидаты отбрасываются, id перенумеровываются до заморозки, чтобы не было дыр.

**Step 4: индекс.** `index.json` собирается из каталога скриптом, `frozen_at` = дата последней заморозки.

**Step 5: проверить**

Run:
```bash
uv run fixjury tasks validate benchmarks --min-tasks 30
```
Expected: `30/30 tasks valid`, код 0. Плюс `python3 -c` сводка: 30 задач, 0 без `sanity.json`, распределение по репозиториям и сложности.

**Step 6: commit** — двумя-тремя коммитами по 10–15 задач (`feat: add corpus tasks T-001..T-010` …), чтобы диффы оставались читаемыми.

---

## Task 1.4 — Генератор черновика спеки

**Objective:** `fixjury tasks draft --repo <url> --issue <n> --id <T-xxx>` присылает issue в LLM по промпту `src/fixjury/prompts/spec_generate.md` и пишет черновик `task.yaml`, который после правки человеком проходит `freeze` и валидатор.

**Files:**
- Create: `src/fixjury/prompts/spec_generate.md` (English)
- Create: `src/fixjury/spec/generator.py` — промпт, вызов LLM, разбор YAML
- Create: `src/fixjury/llm.py` — OpenAI-совместимый chat-completions, заголовок `x-opencode-session`
- Modify: `src/fixjury/config.py` — `llm_base_url`, `llm_model`, `llm_api_key`, `llm_session`, таймаут
- Modify: `src/fixjury/tasks/cli.py` — `tasks draft`
- Create: `tests/unit/test_spec_generator.py` (mock transport)

**Step 1: промпт.** На английском, требует строго один YAML-документ, запрещает выдумывать пути: `allowed_paths` только из списка файлов, переданного отдельно (файлы фикса), `acceptance` — проверяемые утверждения из issue.

**Step 2: клиент.** Минимальный: `complete(messages) -> {text, tokens_in, tokens_out}` через httpx, `Authorization: Bearer`, `x-opencode-session` (PLAN §9), таймаут из конфига.

**Step 3: разбор ответа.** Срезать ```yaml fences, `yaml.safe_load`, `TaskDocument.model_validate` (черновик), при ошибке — один повтор с текстом ошибки, затем отказ.

**Step 4: проверить.** Mock-транспорт: успех, fence, невалидный YAML, повтор; на живой модели — 5 задач корпуса, черновик после правки проходит `freeze` (не коммитим ключ, прогон документируем в отчёте).

**Step 5: commit** — `feat: generate RepairSpec drafts from issues`.

---

## VERIFICATION CHECKPOINT (после фазы 1)

Фактический результат, проверенный на этой ветке:

- [x] `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q` → зелёно, 88 тестов
- [x] `uv run fixjury tasks validate benchmarks` → `28/28 tasks valid`
- [x] у всех 28 задач есть `sanity.json` с четырьмя ожидаемыми исходами (base_checks 0, base_heldout ≠ 0, fix_checks 0, fix_heldout 0)
- [x] `fixjury tasks freeze` на уже замороженной задаче отказывается перезаписывать файл — проверено живым прогоном на GitHub (`error: ... already exists; a frozen task is never rewritten`)
- [x] CI-job `check` валидирует корпус (`uv run fixjury tasks validate benchmarks`)
- [ ] 30 задач вместо 28: кандидаты кончились. Воронка: 120 кандидатов → 83 черновика → 28 sane. Отклонены 55: 43 — в тронутом фиксом тестовом файле есть падающий тест уже на `base_commit`, 8 — не ставятся тестовые зависимости репозитория, 3 — held-out проходит на базе (тест не покрывает дефект), 1 — прочее. Добрать 2 задачи = расширить список целевых репозиториев и повторить отбор, механика уже есть.

## Как воспроизвести корпус

```bash
uv run python scripts/corpus_sanity.py --corpus benchmarks --jobs 3
uv run fixjury tasks validate benchmarks
```

Sanity-прогон пишет `benchmarks/heldout/<id>/sanity.json`; `validate` отказывается принимать задачу, у которой записи нет или `base_commit`/`fix_commit` в ней не совпадают с `task.yaml`.

---

## Решения, принятые в этой фазе

- Held-out тесты — **overlay**: post-fix копии тестовых файлов, которые копируются поверх checkout верификации. Патч, добавляющий файлы, не подходит: upstream-фиксы почти всегда *правят* существующие тесты (в первой выборке 0 добавленных против 35 изменённых), а патч к тому же конфликтует с патчем агента. Overlay переиспользует upstream-тесты как есть и не конфликтует ни с чем.
- `reference.patch` хранится только там, где нет `fix_commit`; иначе эталон — сам `fix_commit` из задачи.
- `base_commit` = первый родитель фикса: это merge-base с основной веткой, а не голова ветки и не дата issue.
- Черновики (`benchmarks/drafts/`) коммитятся: видно, что предложила модель/скрипт и что утвердил человек.
- Sanity корпуса прогоняется в одноразовом venv на хосте; контейнерная верификация — фаза 3. Вердикты из этого прогона не считаются.
- `sanity.json` лежит в самом bundle и проверяется валидатором: задача без доказанного sanity-правила не входит в корпус, а `base_commit`/`fix_commit` в записи должны совпадать с задачей.
- Проверки корпуса идут в CI (`uv run fixjury tasks validate benchmarks`), поэтому подмена bundle или неверная дата ломают сборку, а не только локальный прогон.
