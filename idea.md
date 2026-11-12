# FixJury — платформа оценки и повышения надёжности AI-агентов для разработки

Рекомендуемая концепция Hybrid

## 1. Идея проекта

FixJury — AI Repair Reliability Platform: система, которая оценивает, насколько надёжно автономные AI-агенты исправляют реальные дефекты программного обеспечения.

Вместо создания ещё одного coding agent FixJury позволяет запускать разных агентов на одинаковых инженерных задачах, проверять их исправления, измерять регрессии, оценивать решения людей и сравнивать стоимость полученного результата.

Главный принцип:

> Не измерять, насколько хорошо AI генерирует код. Измерять, сколько реальных инженерных задач он решает, сколько исправлений принимают разработчики и сколько стоит одно принятое исправление.

## 2. Проблема, которую решает проект

Современные coding agents умеют:

- Получать задачи из GitHub Issues.
- Анализировать репозитории.
- Генерировать патчи.
- Запускать тесты.
- Создавать Pull Requests.

Однако успешная генерация патча ещё не означает, что проблема решена.

Остаются вопросы:

- Действительно ли исправлен исходный дефект?
- Не появились ли регрессии?
- Не изменил ли агент лишние файлы?
- Соответствует ли решение первоначальным требованиям?
- Примет ли его независимый разработчик?
- Сколько стоит одно успешно принятое исправление?

FixJury превращает эти вопросы в измеримые показатели.

## 3. Анализ существующих проектов

Исследование показало, что основные составляющие идеи уже существуют. Поэтому FixJury не должен позиционироваться как очередной автономный программист.

| Проект | Что уже реализовано | Что можно взять |
| --- | --- | --- |
| SWE-agent | Решение GitHub Issues агентом | Agent loop, инструменты |
| OpenHands | Платформа автономных coding agents | Agent runtime, sandbox |
| GitHub Copilot Coding Agent | Issue → код → PR | GitHub workflow |
| Sentry Seer | Диагностика production-ошибок | Root cause analysis |
| CodeRabbit | AI code review | Независимый review |
| SWE-bench | Benchmark реальных инженерных задач | Воспроизводимая оценка |

Ссылки на проекты:

- [SWE-agent](<https://github.com/SWE-agent/SWE-agent?utm_source=chatgpt.com>)
- [OpenHands](<https://github.com/OpenHands/OpenHands?utm_source=chatgpt.com>)
- [GitHub Copilot Coding Agent](<https://docs.github.com/en/copilot?utm_source=chatgpt.com>)
- [Sentry Seer](<https://docs.sentry.io/product/ai-in-sentry/seer/?utm_source=chatgpt.com>)
- [CodeRabbit](<https://www.coderabbit.ai/?utm_source=chatgpt.com>)
- [SWE-bench](<https://github.com/SWE-bench/SWE-bench?utm_source=chatgpt.com>)

Вывод анализа: базовый сценарий автономного исправления кода уже занят. Самостоятельная разработка ещё одного универсального coding agent даст мало конкурентного преимущества.

Поэтому FixJury должен использовать существующие агентские решения как исполнителей и специализироваться на оценке качества их работы.

## 4. Новое позиционирование

# AI Repair Reliability Platform

Платформа для измерения надёжности автономного ремонта кода.

1. Запускает разные coding agents на одинаковых задачах.
2. Проверяет результаты по независимым критериям.
3. Выявляет регрессии и нарушения требований.
4. Передаёт исправления человеку на принятие.
5. Сравнивает качество, скорость и стоимость агентов.
6. Использует причины отказов для улучшения последующих запусков.

## 5. Основные возможности

| Модуль | Назначение |
| --- | --- |
| Agent Orchestrator | Запуск и управление агентами |
| Agent Adapters | Подключение SWE-agent, OpenHands и собственного агента |
| RepairSpec | Структурированные требования к исправлению |
| Execution Sandbox | Изолированное выполнение кода |
| Verification Engine | Тесты, lint, type checks, regression checks |
| Independent Reviewer | Проверка соответствия задачи и патча |
| Human Acceptance | Принятие или отклонение исправления |
| Evaluation Lab | Benchmark и сравнение агентов |
| Cost & Observability | Токены, стоимость, логи, время выполнения |
| Dashboard | Визуализация результатов |

### RepairSpec — одна из ключевых особенностей

Каждая задача передаётся агенту в виде проверяемого контракта:

- Описание дефекта.
- Исходный commit.
- Критерии приёмки.
- Ограничения на изменения.
- Обязательные проверки.
- Запрещённые изменения.

Это позволяет оценивать не просто качество кода, а соответствие результата исходной инженерной задаче.

## 6. Как работает FixJury

GitHub Issue / Repair Task

RepairSpec Generator

Критерии приёмки и ограничения

Agent Execution

SWE-agent

OpenHands

Custom

Verification Engine

Tests · Lint · Types · Regression checks

Human Review

Accept / Reject + причина решения

Evaluation Dashboard

Acceptance · Regressions · Time · Cost

## 7. Главные метрики проекта

Это наиболее важная часть FixJury.

| Метрика | Значение |
| --- | --- |
| Human acceptance rate | Доля принятых патчей |
| Regression-free acceptance rate | Доля принятых исправлений без обнаруженных регрессий |
| Cost per accepted fix | Стоимость одного принятого исправления |
| Task success rate | Доля задач, прошедших независимые критерии |
| Median time to repair | Медианное время решения |
| False completion rate | Доля ошибочно объявленных успешными задач |

Особенно важно разделять автоматический успех и человеческое принятие. Прохождение тестов не гарантирует качества решения.

## 8. Self-improving loop

FixJury должен накапливать причины отклонения патчей:

- Изменены лишние файлы.
- Не исправлена первопричина.
- Отсутствует regression test.
- Нарушен публичный API.
- Исправление формально прошло тесты, но неправильно реализует требования.

Затем система предлагает изменения в RepairSpec, prompts и verification policy.

Новые конфигурации допускаются к использованию только после сравнительного benchmark, подтверждающего улучшение без существенных регрессий.

## 9. MVP на 6–8 недель

Для первой версии я бы установил следующие ограничения:

| Параметр | Решение |
| --- | --- |
| Язык | Python |
| Источник задач | GitHub Issues |
| Типы задач | Bug fixes, lint, type errors |
| Агенты | SWE-agent + OpenHands + собственный минимальный baseline |
| Репозитории | Небольшие публичные Python-проекты |
| Benchmark | 50–100 задач |
| Проверки | pytest, Ruff, mypy |
| Интеграция | GitHub PR |
| Интерфейс | Dashboard |
| Безопасность | Изолированное выполнение |
| Принятие изменений | Только человеком |

Не нужно создавать собственную универсальную платформу для программирования. Ценность — в сравнении и проверке существующих агентов.

## 10. Технологический стек

| Компонент | Технология |
| --- | --- |
| Backend | Python, FastAPI |
| Agent orchestration | Собственный state machine |
| LLM | API с tool calling и SSE |
| Agent integration | SWE-agent, OpenHands |
| Tool protocol | MCP |
| GitHub integration | GitHub API |
| Sandbox | Docker |
| Database | PostgreSQL |
| Queue | Redis |
| Observability | OpenTelemetry |
| Evals | pytest + собственный benchmark runner |
| CI/CD | GitHub Actions |
| Automation | n8n |
| Frontend | Next.js |

## 11. Целевые результаты для портфолио

## Цели MVP

# 3

Сравниваемых агента

# 100

Repair runs

# 50+

Benchmark задач

# 3

Основные метрики качества

Главные результаты:

- Human acceptance rate.
- Regression-free acceptance rate.
- Cost per accepted fix.
  Это плановые ориентиры, а не достигнутые показатели. Целевой acceptance rate и бюджет следует уточнить после первого baseline.

## 12. Что получится в итоге

FixJury должен оставить после себя четыре проверяемых артефакта:

1. GitHub Repository — полноценная реализация платформы, тесты, документация, AGENTS.md и CI/CD.
2. Live Demo — интерфейс сравнения результатов нескольких coding agents.
3. Public Benchmark — набор задач, критерии приёмки и воспроизводимые результаты.
4. Engineering Report — сравнение агентов по качеству, скорости, регрессиям и стоимости.

### Формулировка для будущего CV

> Built FixJury, an agent-agnostic AI repair reliability platform for benchmarking autonomous coding agents. Integrated SWE-agent and OpenHands, implemented spec-driven verification, isolated execution, MCP tooling, and human-in-the-loop acceptance. Evaluated \[N\] real-world repair tasks across \[N\] agents, measuring acceptance rate, regression-free fixes, and cost per accepted fix.

## Итоговое решение

Я бы окончательно зафиксировал FixJury в следующем виде:

- Категория: AI Engineering Infrastructure.
- Продукт: AI Repair Reliability Platform.
- Основной пользователь: разработчик, maintainer или команда, использующая coding agents.
- Главная функция: сравнение и проверка автономного ремонта кода.
- Главное отличие: измерение реального инженерного результата, а не качества генерации.
- Главное конкурентное преимущество, которое предстоит доказать: независимая оценка, человеческое принятие и стоимость надёжного исправления.
- Срок MVP: 6–8 недель вечерами.

Моя рекомендация: строить именно эту версию. Она интереснее очередного AI-разработчика, потому что демонстрирует способность проектировать не только агентов, но и инфраструктуру, которая позволяет доверять их результатам.