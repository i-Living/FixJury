# FixJury — спецификация и план реализации

> Версия: 1.0 (ноябрь 2026). Исходная концепция — [`idea.md`](../idea.md). Публичное описание — [`README.md`](../README.md). Правила работы с кодом — [`AGENTS.md`](../AGENTS.md).
> Документ живой: при изменении решения правится §6 и затронутые разделы, а не только код.

## Содержание

1. [Цель и позиционирование](#1-цель-и-позиционирование)
2. [Границы MVP](#2-границы-mvp)
3. [Метрики](#3-метрики)
4. [Протокол слепой приёмки](#4-протокол-слепой-приёмки)
5. [Гигиена бенчмарка](#5-гигиена-бенчмарка)
6. [Архитектура и решения](#6-архитектура-и-решения)
7. [Модель данных](#7-модель-данных)
8. [RepairSpec и корпус задач](#8-repairspec-и-корпус-задач)
9. [Стек и команды](#9-стек-и-команды)
10. [Структура репозитория](#10-структура-репозитория)
11. [План по фазам](#11-план-по-фазам)
12. [Риски и бюджет](#12-риски-и-бюджет)
13. [Принятые решения и открытые вопросы](#13-принятые-решения-и-открытые-вопросы)
14. [Definition of Done](#14-definition-of-done)

---

## 1. Цель и позиционирование

FixJury — слой доверия к автономному ремонту кода. Платформа запускает разные coding agents на одном и том же наборе реальных дефектов, независимо проверяет результат, собирает слепую человеческую приёмку и считает, сколько принятых исправлений выдал агент и во сколько обошлось одно принятое исправление.

Категория: AI Engineering Infrastructure. Пользователь: разработчик или мейнтейнер, который уже использует coding agents и хочет понимать, можно ли им доверять.

Отличие от существующего:

| Что уже есть | Чего не хватает | Что делает FixJury |
| --- | --- | --- |
| SWE-bench: скрытые unit-тесты | Тесты ≠ приёмка; регрессии и границы изменений не измеряются | Добавляет policy-проверки, held-out тесты и слепую приёмку человеком |
| SWE-agent, OpenHands, Copilot: issue → патч | Нет сравнения на одинаковых задачах с одинаковыми лимитами | Гоняет агентов как взаимозаменяемых исполнителей |
| CodeRabbit и др.: ревью патча | Нет измерения стоимости принятого результата | Считает cost per accepted fix, а не цену токенов |

Главный тезис, который проект должен доказать цифрами: **прохождение тестов и человеческая приёмка расходятся, и это расхождение измеримо**.

## 2. Границы MVP

**Входит в MVP**

- Целевые репозитории: Python, лицензии MIT / Apache-2.0 / BSD-3-Clause.
- Источник задач: GitHub issues, зафиксированный `base_commit`.
- 30 задач в замороженном корпусе.
- 3 исполнителя: SWE-agent, OpenHands, собственный минимальный baseline.
- Проверки: pytest, ruff, mypy, policy (границы изменений), held-out тесты.
- Приёмка: только человек, слепо, по фиксированной рубрике.
- Прогон: 30 задач × 3 агента = 90 пар, максимум 2 попытки на пару.
- Артефакты: репозиторий, публичный бенчмарк (метаданные задач + сырые прогоны), engineering report, CLI + API.
- Статическая read-only страница метрик (Next.js static export) на VPS — входит в MVP, делается последней.

**Не входит (v2+)**

- Self-improving loop (автотюнинг RepairSpec и промптов по причинам отказов). В MVP пишем только таксономию отказов и выводы руками.
- Другие языки, монорепозитории, приватные репозитории.
- Мультитенантность, роли, биллинг, веб-регистрация.
- Автоматический merge, автопуш в целевые репозитории.
- Собственный универсальный coding agent.

**Не является:** заменой CI, code review ботом, менеджером задач.

## 3. Метрики

### 3.1 Базовые предикаты

Для пары (задача, агент):

| Предикат | Определение |
| --- | --- |
| `finished` | Прогон дошёл до конца и оставил непустой дифф. `infra_failed` и `cancelled` не считаются попыткой. |
| `automated_pass` | `tests_pass ∧ lint_pass ∧ type_pass ∧ policy_pass ∧ (heldout_pass ≠ false)`. |
| `declared_success` | Агент сам объявил задачу решённой. Для baseline — финальный ответ без маркера `unable`; для SWE-agent/OpenHands — завершение с патчем без явного отказа. Если адаптер не различает — `null`, и метрика считается только по агентам, где сигнал есть. |
| `accepted` | Хотя бы одна попытка пары получила `human_verdict = accept`. Попасть к человеку может только патч с `automated_pass = true`. |
| `regression_free_accepted` | `accepted ∧ heldout_pass = true ∧ нет изменений вне allowed_paths ∧ ни один тестовый файл не удалён и не ослаблен`. Подмножество `accepted`: policy-часть уже отсекается до человека, held-out и проверка ослабления тестов добавляют то, чего автопроверки репозитория не видят. |

### 3.2 Метрики

Знаменатель долей приёмки и успеха — число **пар** (задача, агент), то есть 90, а не число попыток: иначе агент с ретраями выглядит лучше. У `false_completion_rate` знаменатель другой — число прогонов, где агент объявил успех. Held-out тест в MVP обязателен (§5), поэтому `heldout_pass = null` встречается только как дефект данных: такая пара исключается из `regression_free_acceptance_rate`, а её количество публикуется.

| ID | Формула | Примечание |
| --- | --- | --- |
| `human_acceptance_rate` | `accepted / pairs` | Доля пар, где хотя бы одна попытка принята; 95% доверительный интервал по Wilson |
| `regression_free_acceptance_rate` | `regression_free_accepted / pairs` | Wilson CI |
| `tokens_per_accepted_fix` | `Σ(tokens_in + tokens_out) по всем прогонам / accepted` | Главная провайдеро-независимая метрика: не зависит от тарифа |
| `cost_per_accepted_fix` | `Σ cost(все прогоны) / accepted`, где `cost` считается по одному из basis из §3.2.1 | `null`, если ничего не принято; basis указывается в каждой таблице |
| `task_success_rate` | `automated_pass / pairs` | Автоматический успех отдельно от приёмки |
| `median_time_to_repair` | медиана `finished_at − started_at` по принятым попыткам | В отчёте — медиана и IQR |
| `false_completion_rate` | `(declared_success ∧ ¬accepted) / declared_success` | Считается только по агентам с непустым сигналом; знаменатель публикуется |

### 3.2.1 Стоимость при плоском тарифе

Модели идут через opencode-go (Zen Go) — это подписка, а не оплата за токен, поэтому фактического счёта за прогон не существует. Считаем две величины и в каждой таблице отчёта пишем, какая из них перед нами.

| Basis | Формула | Смысл |
| --- | --- | --- |
| `list_price` | `tokens_in × price_in + tokens_out × price_out + sandbox_seconds / 60 × sandbox_rate` | Расчётная стоимость по прайсу `config/pricing.yaml` (`pricing_version`). Сравнима между агентами; это цена «если платить за токены», а не счёт. |
| `amortized` | `(subscription_usd × доля_окна + Σ sandbox_seconds / 60 × sandbox_rate) / accepted` | Фактические деньги: фиксированная плата за период, отнесённая на принятые фиксы. Верхняя оценка, если подпиской пользуются и для других задач. |

`sandbox_rate` на собственном железе = 0. Стоимости с разными `pricing_version` не сравниваются; если сравниваем — это явно написано в отчёте.

### 3.3 Правила отчётности

1. Один набор задач, одни лимиты (шаги, время, бюджет токенов) для всех агентов; отклонения фиксируются в `runs.config_hash`.
2. Прогоны всех агентов по задаче завершаются до публикации любых метрик: никакого «догнать конкурента после просмотра таблицы».
3. `infra_failed`, `timeout`, `flaky_task` исключаются из метрик, но их количество и причины публикуются. Молча выкинутый прогон запрещён.
4. Все цифры в README, отчёте и demo берутся из `metric_snapshots`, а не набираются руками.
5. Конфиг снапшота (`config_hash`) включает: список задач, список агентов с версиями, модель, лимиты, `pricing_version`, версии ruff/mypy/pytest.

## 4. Протокол слепой приёмки

**Что видит ревьюер:** текст issue, RepairSpec, дифф патча, результаты автопроверок (без имён агентов), форму отказа.

**Что скрыто:** имя агента, модель, стоимость, время выполнения, попытка №, остальные попытки той же пары, вердикты по другим агентам.

**Порядок:** для каждой задачи патчи всех агентов перемешиваются с сохранением seed в `review_batches.order_seed`, чтобы слепоту можно было проверить позже. Ревьюер получает батчи по 10 пар, не более 20 вердиктов за сессию; время на вердикт пишется в `verdicts.review_seconds`.

**Рубрика** (5 критериев, каждый 0–2, максимум 10):

| № | Критерий | 0 | 2 |
| --- | --- | --- | --- |
| 1 | Исправлена первопричина, а не симптом | нет | да, объяснимо из диффа |
| 2 | Границы изменений | затронуто лишнее | только необходимые файлы |
| 3 | Регрессионный тест | нет теста | тест падал до фикса и покрывает дефект |
| 4 | Соблюдение контракта RepairSpec | нарушены запреты/API | запреты не нарушены |
| 5 | Читаемость для независимого разработчика | требует переписывания | минимально и идиоматично |

**Правило приёма механическое:** `accept`, если сумма ≥ 8 и в критериях 1 и 4 нет нулей. Иначе `reject` + код из таксономии. «Нравится/не нравится» не является основанием.

**Таксономия отказов** (одна или несколько на вердикт, поле `rejections.code`):

| Код | Смысл |
| --- | --- |
| `WRONG_ROOT_CAUSE` | исправлен симптом |
| `INCOMPLETE_FIX` | фикс частичный, кейс остаётся |
| `EXTRA_FILES` | изменения вне необходимых файлов |
| `NO_REGRESSION_TEST` | нет теста, закрепляющего фикс |
| `API_BREAK` | сломано публичное поведение/подпись |
| `SPEC_VIOLATION` | нарушен запрет из RepairSpec |
| `TEST_OVERFIT` | тест подогнан под патч или ослаблен |
| `BUILD_BREAK` | не собирается/не проходит проверки |
| `UNREADABLE_DIFF` | бинарный, обрезанный или невоспроизводимый дифф |
| `TIMEOUT_LIMIT` | упёрся в лимит шагов/времени |

**Повторная приёмка (intra-rater).** Второго ревьюера нет, поэтому согласие меряется на себе: 9 из 90 пар (10%) повторно принимаются вслепую через ≥ 3 дня, без доступа к первому вердикту (`review_batches.pass_no = 2`, другой `order_seed`). Считается Cohen's kappa intra-rater и agreement rate; результат называется именно так, без слов «межревьюерское согласие». Порог «приемлемо» — κ ≥ 0.6; ниже — пишется как ограничение метода. Расхождения разбираются после подсчёта, итог фиксируется как `verdicts.human_resolution` (`kept` / `changed`).

## 5. Гигиена бенчмарка

**Заморозка задачи.** `task.yaml` создаётся один раз: репозиторий, `base_commit` (merge-base задачи), снимок текста issue, лицензия, критерии приёмки, обязательные проверки, запреты. После первого прогона файл неизменяем; правка = новая версия задачи (`T-014-v2`) и новый прогон.

**Sanity-проверки задачи до включения в корпус:**
1. `base_commit` проходит lint/type-проверки из спеки (иначе шум).
2. Held-out тест обязателен: падает на `base_commit` и проходит на эталонном фиксе (`fix_commit` из апстрима, а если его нет — фикс, написанный вручную и сохранённый в `benchmarks/heldout/<id>/reference.patch`).
3. Пустой дифф (no-op baseline) проходит все проверки: задача не флакует.
4. Размер эталонного фикса 5–80 изменённых строк.

**Contamination.** Жёсткое правило корпуса: issue создан и апстрим-фикс смержен не раньше **2025-06-01**; обе даты пишутся в `task.yaml` и проверяются валидатором, задача с более ранней датой в корпус не берётся. Поле `cutoff_assumption` остаётся для аудита (`clean` по построению): доля задач с неподтверждённой датой должна быть нулевой, иначе это дефект корпуса, а не ограничение метода. Остаточный риск «модель знала фикс» всё равно остаётся и выносится в ограничения отчёта.

**Лицензии и этика.** Только MIT / Apache-2.0 / BSD-3-Clause, SPDX-идентификатор в задаче. Никаких PR в чужие репозитории: работаем на форках и в локальных контейнерах, отчёт публикуется как данные, а не как спам-патчи.

**Held-out тесты.** Лежат в этом же репозитории, в `benchmarks/heldout/<task-id>/`. Они не секретные: изоляция обеспечивается не тайной, а тем, что агент работает в checkout **целевого** репозитория на `base_commit` и без сети — каталог `benchmarks/` в его контейнер не попадает вообще, FixJury-репозиторий не монтируется. Верификация монтирует `benchmarks/heldout/<id>/` отдельным read-only томом. CI-job `guard` проверяет, что контейнер агента не получает монтирований из `benchmarks/`.

**Версии инструментов.** ruff/mypy/pytest пинятся на задачу и пишутся в `verdicts.tool_versions`: иначе смена версии молча переписывает старые вердикты.

**Публикация.** Публикуются: `benchmarks/index.json`, `task.yaml`, `benchmarks/heldout/`, сырые `runs`/`verdicts` в JSONL, диффы с указанием исходного репозитория и лицензии, отчёт с воспроизводимыми командами и статическая страница метрик на VPS.

## 6. Архитектура и решения

### 6.1 Компоненты

| Компонент | Ответственность | Модуль |
| --- | --- | --- |
| Corpus loader | валидация и заморозка задач | `src/fixjury/tasks/` |
| Spec generator | issue → RepairSpec (LLM), рендер YAML | `src/fixjury/spec/` |
| Sandbox | контейнер на прогон, лимиты, изоляция, tool API | `src/fixjury/sandbox/` |
| Adapters | SWE-agent, OpenHands, baseline | `src/fixjury/agents/` |
| Verification | pytest/ruff/mypy/policy/held-out, сборка вердикта | `src/fixjury/verify/` |
| Review | слепые батчи, рубрика, таксономия, API | `src/fixjury/review/` |
| Metrics | формулы, стоимость, снапшоты | `src/fixjury/metrics/` |
| Report | markdown + графики | `src/fixjury/report/` |
| API/CLI | FastAPI, typer | `src/fixjury/api/`, `src/fixjury/cli.py` |
| Queue | Redis, воркер, ретраи, resume | `src/fixjury/queue.py` |

### 6.2 Поток

```
task.yaml (frozen)
   │
   ▼
RunSpec (task + agent + limits + pricing_version)  ──► Redis queue ──► worker
   │
   ▼
sandbox: repo@base_commit, no network, limits
   │   agent works (model in platform process for baseline,
   │   in-container + egress proxy for third-party agents)
   ▼
diff (git add -N . && git diff --binary) + tokens + sandbox_seconds
   │
   ▼
verification container: base_commit + diff, tool versions pinned,
mounted held-out tests, no agent code
   │
   ▼
verdict (automated) ──► review batches (blind) ──► human verdicts
   │
   ▼
metrics → metric_snapshots → report / dashboard
```

### 6.3 Решения

| № | Решение | Почему | Следствия |
| --- | --- | --- | --- |
| 1 | Дифф — единственный транспорт между агентом и верификацией | агент не может подменить результаты проверок; патч воспроизводим | бинарные и нечитаемые диффы отклоняются с кодом `UNREADABLE_DIFF` |
| 2 | Верификация в свежем контейнере от `base_commit` | контейнер агента мог быть изменён (тесты, конфиги, кэш) | +1 контейнер на прогон, зато результат не зависит от поведения агента |
| 3 | У baseline модель вызывается в процессе платформы, инструменты — в контейнере | песочница остаётся без сети | tool API узкий: exec, read, write, list; без шелл-инъекций |
| 4 | Сторонние агенты работают в контейнере с egress-прокси только к LLM | их архитектура требует сети изнутри | прокси-allowlist из одного хоста, DNS выключен, логируется |
| 5 | Песочница без сети, без docker.sock, с лимитами CPU/RAM/pids/времени | прогон не должен влиять на хост и на другие прогоны | лимиты в RunSpec, убийство контейнера по общему таймауту |
| 6 | Held-out тесты в этом же репозитории, монтируются только в верификацию | агент работает в checkout целевого репо на `base_commit` и без сети, поэтому каталог ему недоступен; отдельный приватный репо — лишняя инфраструктура для соло-MVP | тесты публичны, это записано в ограничениях; CI-guard проверяет отсутствие монтирований из `benchmarks/` в контейнер агента |
| 7 | Слепая приёмка с сохранённым seed и очередью батчей | слепота должна быть проверяема, а не декларируема | `review_batches` — часть данных, не UI-состояние |
| 8 | Метрики считаются один раз и пишутся в `metric_snapshots` с `config_hash` | отчёт и UI не должны расходиться | любая правка формулы = новая версия снапшота |
| 9 | Стоимость считается двумя basis: `list_price` (токены по прайсу) и `amortized` (доля подписки), плюс `tokens_per_accepted_fix` | модели идут через плоский тариф opencode-go, фактического счёта за прогон нет | basis указывается в каждой таблице отчёта; `pricing_version` на каждом прогоне, кросс-версионное сравнение запрещено |
| 10 | Self-improving loop вне MVP | без стабильного бенчмарка это измерение шума | в MVP только таксономия отказов и ручные выводы в отчёте |

## 7. Модель данных

PostgreSQL, SQLAlchemy 2.0, Alembic. Шесть таблиц.

**`tasks`** — `id` text PK (`T-001`), `repo_url`, `license` (SPDX), `base_commit` char(40), `fix_commit` char(40) null, `fix_merged_at` date null, `issue_number` int, `issue_url`, `issue_created_at` date, `title`, `body` text, `difficulty` enum(easy|medium|hard), `spec` jsonb, `cutoff_assumption` enum(clean|unknown), `heldout_ref` text, `frozen_at` timestamptz, `frozen_by` text.

**`runs`** — `id` uuid PK, `task_id` FK, `agent` text, `agent_version` text, `model` text, `attempt` smallint, `parent_run_id` uuid null, `status` enum(queued|running|finished|infra_failed|cancelled), `declared_success` bool null, `diff` text null, `diff_sha256` char(64), `tokens_in` int, `tokens_out` int, `sandbox_seconds` numeric(10,2), `cost_list_usd` numeric(10,4), `pricing_version` text, `config_hash` char(64), `trace_id` text, `started_at`, `finished_at`. Индексы: `(task_id, agent, attempt) unique`, `(agent, status)`.

**`verdicts`** — `run_id` PK/FK, `tests_pass`, `lint_pass`, `type_pass`, `policy_pass`, `heldout_pass` bool null, `automated_pass` bool, `tool_versions` jsonb, `reviewer_verdict` enum(accept|reject|abstain) null, `reviewer_reasons` text[], `reviewer_model` text, `human_verdict` enum(accept|reject) null, `human_reasons` text[], `human_resolution` enum(kept|changed) null, `human_reviewer` text, `review_seconds` int, `blind` bool default true, `batch_id` text, `reviewed_at` timestamptz.

**`rejections`** — `id` bigserial, `run_id` FK, `source` enum(automated|reviewer|human), `code` enum (таблица кодов в §4), `note` text.

**`review_batches`** — `id` text PK, `task_id` FK, `run_ids` uuid[], `order_seed` bigint, `pass_no` smallint (1 — первый проход, 2 — повторный через ≥3 дня), `created_at`, `reviewer` text.

**`metric_snapshots`** — `id` uuid PK, `computed_at`, `agent` text, `cost_basis` enum(list_price|amortized), `config_hash` char(64), `payload` jsonb (все метрики + CI + счётчики исключений).

## 8. RepairSpec и корпус задач

`benchmarks/tasks/T-001/task.yaml`:

```yaml
id: T-001
repo: https://github.com/pallets/click
license: BSD-3-Clause
base_commit: 8e6b0f0a1c9d4e2f3a5b7c8d9e0f1a2b3c4d5e6f
fix_commit: 3f1c2b4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b
issue:
  number: 2712
  url: https://github.com/pallets/click/issues/2712
  created_at: 2025-09-14
  fix_merged_at: 2025-10-02
title: "Option name with a dash breaks shell completion"
acceptance:
  - "completion output for --foo-bar matches the option name"
  - "no changes outside src/click/shell_completion.py and tests/"
checks:
  - pytest tests/test_shell_completion.py
  - ruff check src tests
  - mypy src
forbidden:
  - "changes to public signatures in src/click/core.py"
  - "edits to files under .github/"
allowed_paths:
  - src/click/shell_completion.py
  - tests/
difficulty: medium
heldout_ref: benchmarks/heldout/T-001
cutoff_assumption: clean
```

Правила:

- Обязательные ключи: `id`, `repo`, `license`, `base_commit`, `issue`, `title`, `acceptance`, `checks`, `allowed_paths`, `difficulty`, `heldout_ref`. Отсутствие ключа — ошибка валидации, а не дефолт. `fix_commit` опционален: если апстрим-фикса нет, эталон лежит в `benchmarks/heldout/<id>/reference.patch`.
- `checks` — только команды из белого списка (`pytest`, `ruff`, `mypy`) с фиксированными аргументами; произвольный shell запрещён.
- `allowed_paths` — единственный источник правды для policy-проверки, `forbidden` уточняет запреты внутри разрешённых путей.
- Генерация: LLM предлагает спеку из issue → человек утверждает в PR на файл `task.yaml` → `fixjury tasks freeze` фиксирует `frozen_at`, `base_commit` и даты.
- Корпус описывается `benchmarks/index.json` (список id, версия корпуса, дата заморозки).
- Валидация в CI: схема, уникальность id, существование `base_commit` (и `fix_commit`, если указан) в репозитории, обе даты не раньше 2025-06-01, наличие `benchmarks/heldout/<id>/`, отсутствие монтирований из `benchmarks/` в контейнер агента.

## 9. Стек и команды

| Слой | Технология |
| --- | --- |
| Язык | Python 3.12, `uv` |
| API | FastAPI + uvicorn |
| БД | PostgreSQL 16, SQLAlchemy 2.0 (async), Alembic |
| Очередь | Redis 7, собственный воркер |
| Песочница | Docker (docker-py) |
| Агенты | SWE-agent, OpenHands, baseline (собственный) |
| Протокол инструментов | MCP для внешних интеграций, внутренний tool API для песочницы |
| Интеграция | GitHub API (issues, форки), без PR в чужие репозитории |
| Наблюдаемость | OpenTelemetry (спаны на прогон и шаг) |
| Отчёт | markdown + matplotlib |
| Дашборд | статический экспорт Next.js (SSG) + Chart.js, раздача nginx с VPS |
| CI | GitHub Actions |

`n8n` из исходной идеи не используется (решение 1a в §13): оркестрация — собственный state machine + Redis, нотификации — несколько строк в воркере.

**LLM-провайдер.** Модели идут через opencode-go (Zen Go), OpenAI-совместимый endpoint; тариф плоский, поэтому стоимость считается по §3.2.1. Zen Go требует заголовок `x-opencode-session` — адаптеры обязаны его отправлять, иначе запрос отклоняется.

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q
docker compose up -d
uv run alembic upgrade head
uv run fixjury tasks validate benchmarks
uv run fixjury run --task T-001 --agent baseline
uv run fixjury bench --tasks benchmarks/index.json --agents baseline,swe-agent,openhands
uv run fixjury report --snapshot <id>
```

## 10. Структура репозитория

```
.
├── README.md
├── AGENTS.md
├── LICENSE
├── idea.md
├── docs/PLAN.md
├── pyproject.toml
├── docker-compose.yml
├── config/pricing.yaml
├── .github/workflows/ci.yml
├── benchmarks/
│   ├── index.json
│   ├── tasks/T-001/task.yaml
│   └── heldout/T-001/           # тесты и reference.patch, агенту не монтируются
├── src/fixjury/
│   ├── config.py  db.py  models.py  cli.py  queue.py
│   ├── tasks/     schema.py loader.py freeze.py validate.py
│   ├── spec/      generator.py render.py
│   ├── agents/    base.py baseline.py swe_agent.py openhands.py registry.py
│   ├── sandbox/   docker.py tools.py policy.py
│   ├── verify/    runner.py checks.py heldout.py
│   ├── review/    package.py rubric.py taxonomy.py api.py
│   ├── metrics/   definitions.py compute.py cost.py
│   ├── report/    render.py charts.py
│   ├── api/       app.py routes.py
│   └── prompts/   spec_generate.md baseline_agent.md
├── tests/unit/  tests/integration/
└── web/         (static export)
```

## 11. План по фазам

Формат задачи: **цель → файлы → приёмка → проверка**. Оценки — вечера, ~10 часов в неделю.

### Фаза 0 — Каркас (неделя 1, ~8 ч)

**0.1 Проект и зависимости.** `uv init`, `pyproject.toml` с зависимостями (fastapi, uvicorn, sqlalchemy[asyncio], asyncpg, alembic, pydantic, pydantic-settings, redis, docker, typer, httpx, opentelemetry-sdk, jinja2, matplotlib; dev: pytest, pytest-asyncio, ruff, mypy, pre-commit).
*Проверка:* `uv sync && uv run python -c "import fixjury"` → без ошибок.

**0.2 Линтер, типы, тесты.** Конфиг ruff (line-length 100, target py312), mypy (strict для `src`), pytest (`unit`, `integration` маркеры), один smoke-тест.
*Проверка:* `uv run ruff check . && uv run mypy src && uv run pytest -q` → 1 passed.

**0.3 Инфраструктура.** `docker-compose.yml`: postgres:16, redis:7, healthcheck, volume, `.env.example`.
*Проверка:* `docker compose up -d && docker compose ps` → оба healthy.

**0.4 Схема БД.** Модели из §7, `alembic init`, первая миграция.
*Проверка:* `uv run alembic upgrade head`; `docker compose exec postgres psql -U fixjury -c '\dt'` → 6 таблиц.

**0.5 CI.** `.github/workflows/ci.yml`: uv sync → ruff → format check → mypy → pytest.
*Проверка:* зелёный прогон на push.

### Фаза 1 — Корпус и RepairSpec (неделя 2, ~10 ч)

**1.1 Схема задачи.** pydantic-модель `task.yaml` из §8, команда `fixjury tasks validate`.
*Проверка:* `uv run fixjury tasks validate benchmarks` → `30/30 valid` (после 1.3); файл с датой раньше 2025-06-01 или без `benchmarks/heldout/<id>/` даёт понятную ошибку.

**1.2 Заморозка.** `fixjury tasks freeze --repo <url> --issue <n>`: снимает issue body и обе даты, `fix_commit`, вычисляет merge-base (`base_commit`), лицензию, пишет `task.yaml` с `frozen_at`.
*Проверка:* повторный запуск на той же задаче отказывается перезаписывать замороженный файл.

**1.3 Корпус из 30 задач.** Отбор по §5: Python, MIT/Apache/BSD, явные критерии в issue, эталонный фикс 5–80 строк, есть тесты, issue создан и фикс смержен не раньше 2025-06-01.
*Проверка:* 30 валидных `task.yaml`, `benchmarks/index.json` собран, у всех задач есть `benchmarks/heldout/<id>/`.

**1.4 Генератор спеки.** LLM из issue → черновик `task.yaml`, человек утверждает в PR.
*Проверка:* на 5 задачах черновик проходит валидацию после ручной правки; промпт в `src/fixjury/prompts/spec_generate.md`.

**1.5 Held-out тесты.** Каталоги `benchmarks/heldout/<id>/` в этом же репозитории, загрузчик по `heldout_ref`, монтирование только в контейнер верификации.
*Проверка:* sanity-правило из §5 выполнено для всех 30 задач; CI-guard падает, если контейнер агента получает монтирование из `benchmarks/`.

### Фаза 2 — Песочница и baseline (неделя 3, ~10 ч)

**2.1 Sandbox.** Образ на задачу, `network_mode=none`, read-only rootfs + tmpfs, лимиты CPU/RAM/pids, общий таймаут, `finally: remove(force=True)`.
*Проверка:* прогон `true` внутри контейнера; `curl` внутри падает по отсутствию сети; контейнер не остаётся в `docker ps`.

**2.2 Tool API.** exec/read/write/list внутри контейнера, без шелл-склейки, лимит вывода.
*Проверка:* unit-тесты на экранирование и лимиты.

**2.3 Baseline-агент.** Модель в процессе платформы, шаги ограничены, финальный ответ — дифф или `unable`.
*Проверка:* `fixjury run --task T-001 --agent baseline` создаёт `runs` с `diff_sha256` и `tokens_*`.

**2.4 Захват диффа.** `git add -N . && git diff --binary --no-color`, sha256, отклонение бинарных диффов.
*Проверка:* untracked-файл попадает в дифф; бинарный патч даёт `UNREADABLE_DIFF`.

**Чекпоинт:** `docker ps` чист, egress = 0, один прогон воспроизводим побайтово при одинаковом ответе модели (мок).

### Фаза 3 — Верификация (неделя 4, ~10 ч)

**3.1 Runner проверок.** Свежий контейнер от `base_commit`, применение диффа, команды из `checks` в корне репозитория, фиксированные версии инструментов.
*Проверка:* `fixjury verify --run <id>` идемпотентен, повторный запуск даёт тот же вердикт.

**3.2 Held-out.** Монтирование `benchmarks/heldout/<id>/` только здесь, read-only; `heldout_pass` может быть `null`.
*Проверка:* прогон без монтирования даёт `null`, а не `false`.

**3.3 Policy.** `allowed_paths`, лимит числа файлов, запрет правки CI/конфигов/лок-файлов, запрет удаления тестов.
*Проверка:* по одной негативной фикстуре на каждое правило.

**3.4 Вердикт.** Сборка `automated_pass`, запись `tool_versions`, `rejections` для автоматических отказов.
*Проверка:* 5 фикстур «плохих патчей» (по одной на чек) отклонены именно своим чеком.

### Фаза 4 — Реальные агенты и очередь (неделя 5, ~10 ч)

**4.1 SWE-agent адаптер.** Пин версии, контейнер + egress-прокси к LLM, парсинг диффа, бюджет токенов.
*Проверка:* мок-LLM прогон в CI; реальный прогон одной задачи.

**4.2 OpenHands адаптер.** То же, другой формат вывода.
*Проверка:* `fixjury run --task T-001 --agent openhands` даёт `finished` или явный `infra_failed` с причиной.

**4.3 Очередь и воркер.** Redis, ретраи (max 2 попытки), `--resume`, запрет дублей по `(task, agent, attempt)`.
*Проверка:* kill воркера посреди прогона → `--resume` продолжает без дублей.

**4.4 Трейсы и стоимость.** OTel-спаны на прогон и шаг; `tokens_*` и `cost_list_usd` по §3.2.1 с `pricing_version`; доля подписки считается в снапшоте.
*Проверка:* сумма `cost_list_usd` в `runs` совпадает со снапшотом; в отчёте присутствуют оба basis; трейс виден в консоли OTel.

**Чекпоинт:** бенч 5 задач × 3 агента завершается, ≤1 infra_failed, отчёт по сырым данным строится.

### Фаза 5 — Прогон бенчмарка (неделя 6, ~10 ч)

**5.1 Пилот.** 5 задач × 3 агента, калибровка лимитов шагов и времени, проверка бюджета.
**5.2 Полный прогон.** 30 × 3, до 2 попыток, мониторинг бюджета, автоматический стоп при превышении cap.
**5.3 Гигиена данных.** Причины исключений, экспорт `runs`/`verdicts` в JSONL, проверка воспроизводимости из БД.
*Проверка:* 90 пар завершены, `infra_failed` ≤10% от всех запусков (каждый с причиной), экспорт совпадает с БД по хешам.

### Фаза 6 — Слепая приёмка (неделя 7, ~10 ч)

**6.1 Упаковка батчей.** Перемешивание, удаление agent/model/cost/time, нормализация заголовков диффа, `order_seed`.
**6.2 UI приёмки.** Jinja2-страница: issue, спека, дифф, результаты чеков, рубрика, кнопки accept/reject, таксономия.
**6.3 Приёмка.** 90 вердиктов, батчи по 10, время на вердикт записано.
**6.4 Повторная приёмка.** 9 пар через ≥3 дня, второй проход вслепую (`pass_no = 2`, другой `order_seed`), intra-rater kappa и agreement rate, фиксация расхождений.
*Проверка:* 90 вердиктов первого прохода + 9 второго в БД; слепота проверяема по `review_batches`; kappa посчитан или записан как ограничение.

### Фаза 7 — Метрики, отчёт, демо (неделя 8, ~10 ч)

**7.1 Метрики.** Формулы §3.2, Wilson CI, `metric_snapshots` с `config_hash`.
**7.2 Отчёт.** `docs/report/README.md` + графики: acceptance по агентам, cost per accepted fix, доля регрессий.
**7.3 Дашборд.** Статический экспорт из `metric_snapshots` (несколько графиков + таблица), раздача через nginx на VPS, обновление скриптом при новом снапшоте.
**7.4 Финализация.** README с реальными цифрами, CV-строка, публикация корпуса и сырых прогонов.
*Проверка:* `fixjury report --snapshot <id>` воспроизводит отчёт; числа в README совпадают со снапшотом.

## 12. Риски и бюджет

| Риск | Как снимаем |
| --- | --- |
| Адаптеры SWE-agent/OpenHands ломаются при обновлении | пин версий в lock, smoke-тест на мок-LLM в CI |
| Бюджет прогонов выше ожидаемого | лимиты подписки и время прогона: растягиваем прогон по времени, а не меняем лимиты агентов; автостоп по общему времени |
| Флаки тесты в целевых репозиториях | no-op baseline до старта, задачи с флаком выкидываются с записанной причиной |
| Contamination завышает acceptance | жёсткое правило 2025-06-01 для обеих дат, валидация в CI, остаточный риск в ограничениях отчёта |
| Ручная приёмка съедает время | 30 задач вместо 50–100, батчи по 10, механическое правило приёма |
| Held-out тесты публичны | принято осознанно (решение 3b): агент работает в checkout целевого репо без сети, каталог не монтируется; ограничение записано в отчёте |
| Плоский тариф искажает денежную метрику | `tokens_per_accepted_fix` — главная метрика, `cost_per_accepted_fix` в двух basis (`list_price`, `amortized`) |
| Контейнеры текут, диск забит | reap при старте, `finally: remove(force=True)`, лимит на размер образа |
| Субъективность приёмки | слепота, рубрика, таксономия, повторная приёмка через ≥3 дня, intra-rater kappa |
| Невоспроизводимость из-за версий | версии инструментов в вердикте, `config_hash`, `pricing_version` |

**Бюджет (оценка, не измерение).** Модели идут через подписку opencode-go, поэтому фактические списания за прогоны равны нулю; ограничение — лимиты подписки и время хоста. До 180 прогонов (90 пар × до 2 попыток). Расчётная стоимость по прайсу (basis `list_price`) публикуется как оценка «если платить за токены», а не как счёт. Песочница на своём железе: `sandbox_rate = 0`.

## 13. Принятые решения и открытые вопросы

### 13.1 Решения

| № | Вопрос | Решение | Следствие |
| --- | --- | --- | --- |
| 1a | n8n | убран из стека | оркестрация — state machine + Redis, нотификации — в воркере |
| 2a | лицензия | MIT, файл `LICENSE` | код и корпус под MIT; SPDX в задачах — только совместимые (MIT/Apache-2.0/BSD-3-Clause) |
| 3b | held-out тесты | в этом репозитории, `benchmarks/heldout/<id>/` | публичны; изоляция — checkout целевого репо + отсутствие сети, CI-guard на монтирование |
| 4b | второй ревьюер | нет, работаем соло | повторная слепая приёмка 9 пар через ≥3 дня, intra-rater kappa, без формулировки «межревьюерское согласие» |
| 5b | модели | opencode-go (Zen Go), плоский тариф | фактические списания 0; `tokens_per_accepted_fix` — главная метрика, деньги в двух basis |
| 6a | contamination | issue создан и фикс смержен не раньше 2025-06-01 | жёсткий фильтр корпуса, валидация в CI |
| 7a | дашборд | статический экспорт из снапшотов на VPS | входит в MVP последней задачей, серверная часть не нужна |
| 8a | публикация | публичная страница + корпус, сырые прогоны, диффы с атрибуцией | демо на VPS, отчёт воспроизводим из данных |

### 13.2 Осталось открытым

1. Какая именно модель opencode-go гоняется в MVP — фиксируется на пилоте (задача 5.1) и пишется в `runs.model`.
2. Лимиты подписки (rate limit и окно) — проверяются на пилоте; при упоре растягиваем прогон по времени, а не меняем лимиты агентов.
3. URL демо: поддомен существующего домена владельца или `fixjury.dev` (домен свободен, но регистрация — отдельное решение).

## 14. Definition of Done

MVP считается сданным, когда одновременно выполнено:

1. Корпус: 30 замороженных задач, валидация в CI, held-out тесты в `benchmarks/heldout/`, в контейнер агента не монтируются.
2. Прогон: 90 завершённых пар (30 × 3); `infra_failed` ≤10% от всех запусков и у каждого записанная причина; все прогоны в БД с диффами.
3. Приёмка: 90 человеческих вердиктов по рубрике, таксономия отказов заполнена, повторная приёмка 9 пар через ≥3 дня, intra-rater kappa посчитан или ограничение зафиксировано.
4. Метрики: 7 метрик (включая `tokens_per_accepted_fix`) посчитаны из БД, деньги в двух cost basis, снапшот с `config_hash` и `pricing_version`.
5. Отчёт: `docs/report/` с цифрами, графиками, ограничениями и командами воспроизведения.
6. Репозиторий: README с реальными цифрами, `AGENTS.md` актуален, `LICENSE` (MIT), CI зелёный, `uv run ruff check . && uv run mypy src && uv run pytest -q` проходит.
7. Публикация: корпус, сырые прогоны, отчёт и статическая страница метрик доступны по ссылке.
