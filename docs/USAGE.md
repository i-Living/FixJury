# FixJury — как пользоваться системой

Практическая инструкция для оператора: от чистого клона до опубликованного отчёта.
Спецификация и обоснования решений — в [`PLAN.md`](PLAN.md); правила для тех, кто меняет код, —
в [`AGENTS.md`](../AGENTS.md). Если этот файл расходится с кодом, прав код, а этот файл чинится
в том же изменении.

Все команды выполняются **из корня репозитория**: `pydantic-settings` читает `.env` из текущего
каталога, а пути к корпусу по умолчанию — относительные (`benchmarks`).

---

## 1. Что делает система

FixJury не чинит код сам. Он берёт замороженную задачу (issue + `base_commit`), запускает на ней
агента в песочнице, получает **только унифицированный diff**, проверяет его в отдельном свежем
контейнере, отдаёт человеку на слепое ревью и считает метрики из базы.

```
задача (benchmarks/tasks/T-xxx/task.yaml)
  → fixjury run      агент в контейнере, результат = diff + токены + время
  → fixjury verify   свежий контейнер: base_commit + diff, pytest / ruff / mypy / policy / held-out
  → fixjury review   слепые батчи, вердикты человека
  → fixjury metrics  снимок метрик (metric_snapshots)
  → fixjury report | dashboard | export
```

Каждый шаг пишет в PostgreSQL. Ни один шаг не пересчитывает то, что уже посчитан: отчёт и страница
читают готовые `metric_snapshots`.

---

## 2. Требования

| Что | Версия | Зачем |
| --- | --- | --- |
| Python + `uv` | 3.12 | всё приложение; `uv sync` создаёт `.venv` |
| Docker Engine | **26.0.0+** | песочницы и сети прогонов; более старые движки форвардят DNS из internal-сетей |
| `git` | любая свежая | клонирование целевых репозиториев и захват diff |
| Node + `npm` | 20.9+ | только `fixjury dashboard` и `npm ci` в `web/` |
| Сеть | доступ к github.com и к LLM-эндпоинту | первый клон каждого целевого репозитория, вызовы модели |

Проверить движок: `docker version` — строка `Server: Docker Engine` должна быть ≥ 26.

---

## 3. Установка (один раз)

```bash
cp .env.example .env          # затем отредактировать
docker compose up -d          # postgres 127.0.0.1:55432, redis 127.0.0.1:16379
uv sync
uv run alembic upgrade head
```

Проверка, что всё поднялось:

```bash
uv run fixjury version                       # 0.1.0
uv run fixjury tasks validate benchmarks     # 32/32 tasks valid
docker compose ps                            # оба сервиса healthy
```

### 3.1 Что заполнить в `.env`

`docker-compose.yml` публикует порты только на `127.0.0.1`, поэтому дефолтные
`FIXJURY_DATABASE_URL` и `FIXJURY_REDIS_URL` менять не нужно.

| Переменная | Когда нужна | Смысл |
| --- | --- | --- |
| `FIXJURY_LLM_BASE_URL`, `FIXJURY_LLM_MODEL`, `FIXJURY_LLM_API_KEY` | прогоны `baseline` и `mini` | модель, которую вызывает агент |
| `FIXJURY_LLM_SESSION` | **обязательна для opencode-go** | значение заголовка `x-opencode-session`; без него Zen Go отказывает, а прогон падает как `infra_failed` без внятной причины |
| `FIXJURY_MINI_IMAGE`, `FIXJURY_MINI_VERSION`, `FIXJURY_MINI_COMMAND` | прогоны `mini` | образ харнесса и его версия; пустой образ = «не настроено», адаптер откажется работать |
| `FIXJURY_OPENHANDS_IMAGE`, `..._COMMAND`, `..._VERSION` | прогоны `openhands` | то же для OpenHands |
| `FIXJURY_GITHUB_TOKEN` | `tasks draft` и `tasks freeze` | поднимает лимит GitHub с 60 до 5000 запросов в час |
| `FIXJURY_CHECKOUT_CACHE` | по желанию | каталог зеркал целевых репозиториев; без него — временный каталог платформы |
| `FIXJURY_SANDBOX_*` | по желанию | CPU, память, таймаут прогона (по умолчанию 2 CPU / 2048 МБ / 1200 с) |
| `FIXJURY_PRICING_FILE` | для стоимости | по умолчанию `config/pricing.yaml`, ставки и `pricing_version` |
| `FIXJURY_OTEL_CONSOLE` | отладка | `1` — печатать OTel-спаны в консоль |

Ключи в репозиторий не коммитятся, `.env` в `.gitignore`.

### 3.2 Образ mini-swe-agent

`mini` — сторонний агент, он живёт в своём образе. Собирается один раз, пин версии — в Dockerfile:

```bash
docker build -t fixjury-mini:2.4.6 dockerfiles/mini
```

Затем в `.env`:

```dotenv
FIXJURY_MINI_IMAGE=fixjury-mini:2.4.6
FIXJURY_MINI_VERSION=2.4.6
```

Версию в теге и в `FIXJURY_MINI_VERSION` меняют **вместе** с пином в Dockerfile: прогоны под разными
версиями харнесса несравнимы, и это должно быть видно в данных, а не в чьей-то памяти.

---

## 4. Рабочий цикл целиком

```bash
# 1. корпус
uv run fixjury tasks validate benchmarks --min-tasks 32

# 2. прогоны + проверка (baseline уже настроен, mini — после раздела 3.2)
uv run fixjury bench --agents baseline,mini --verify --max-seconds 28800

# 3. слепое ревью
uv run fixjury review pack --reviewer owner --pass 1
uv run fixjury review serve --host 127.0.0.1 --port 8099
#    … открыть http://127.0.0.1:8099 и пройти батчи …

# 4. метрики и публикация
uv run fixjury metrics compute --agent baseline --cost-basis list_price
uv run fixjury metrics compute --agent mini     --cost-basis list_price
uv run fixjury report --charts
(cd web && npm ci)                              # один раз, для dashboard
uv run fixjury dashboard
uv run fixjury export
```

Порядок обязателен только в одном месте: `metrics compute` читает `runs` + `verdicts` + `rejections`,
поэтому запускать его до `verify` и до ревью бессмысленно — приёмка останется `null`.

---

## 5. Корпус задач

Задача — каталог `benchmarks/tasks/T-xxx/task.yaml`; held-out тесты и эталонный патч —
`benchmarks/heldout/T-xxx/`. Список задач для `bench` по умолчанию берётся из индекса корпуса.

### 5.1 Проверка корпуса

```bash
uv run fixjury tasks validate benchmarks
uv run fixjury tasks validate benchmarks --min-tasks 32   # упадёт, если задач меньше
```

Проверяются схема каждого `task.yaml`, наличие held-out бандла и согласованность индекса. Вывод —
по одной строке на находку и итог `32/32 tasks valid`; код возврата 1 при любой находке.

### 5.2 Черновик задачи из issue

```bash
uv run fixjury tasks draft \
  --repo https://github.com/pallets/click --issue 2712 --id T-101 --fix-pr 2715
```

Черновик пишется в `benchmarks/drafts/T-101.yaml` (или `--out`). Для этого нужны
`FIXJURY_GITHUB_TOKEN` и `FIXJURY_LLM_*`. `--fix-pr` — **слитый** PR с апстрим-фиксом: из его файлов
выводятся `allowed_paths`. `--dry-run` печатает результат, ничего не записывая.

### 5.3 Заморозка

```bash
uv run fixjury tasks freeze --draft benchmarks/drafts/T-101.yaml --dry-run   # посмотреть
uv run fixjury tasks freeze --draft benchmarks/drafts/T-101.yaml             # записать
```

`freeze` подтягивает с GitHub лицензию, `base_commit` (родитель слитого фикса; проверяется, что это
merge-base, а не голова ветки), `fix_commit` и даты, и пишет `benchmarks/tasks/T-101/task.yaml`. Сначала
— `--dry-run`: в задачу попадают факты, которые потом нельзя исправить.

**Замороженное не редактируется.** Если после первого прогона поменять `task.yaml`, сравнение
инвалидируется; правильный путь — новая версия задачи (новый id). `freeze` откажется перезаписывать
существующий файл.

---

## 6. Один прогон: `fixjury run`

```bash
uv run fixjury run --task T-001 --agent baseline
uv run fixjury run --task T-001 --agent baseline --attempt 2 --keep-workspace
```

Что происходит: платформа клонирует `repo@base_commit` (сначала в зеркало-кэш, дальше локально),
запускает агента в контейнере (наружу он не выходит — см. ниже), забирает
`git add -N . && git diff --binary`, пишет строку в `runs` с токенами, временем, `diff_sha256`,
`config_hash`, `pricing_version` и `trace_id`.

Вывод:

```
run 3f1c2b4d-… finished agent=baseline model=deepseek-v4.1-flash tokens=15230/4180 diff=2841B sha256=9a0c…
```

| Опция | Смысл |
| --- | --- |
| `--agent` | `baseline`, `mini`, `openhands` (по умолчанию `baseline`) |
| `--attempt N` | номер попытки; пара (задача, агент, попытка) уникальна |
| `--image` | переопределить образ песочницы для этого прогона |
| `--keep-workspace` | оставить checkout на диске для разбора |
| `--corpus` | другой корень корпуса |

Коды возврата: `0` — прогон `finished`, `1` — статус иной (`infra_failed`, `cancelled`), `2` — ошибка
подготовки (нет задачи, неизвестный агент, не настроен образ харнесса).

Что нужно знать:

- **Наружу контейнер не выходит.** У baseline сети нет вовсе (модель вызывает платформа, в контейнере
  исполняются только инструменты); `mini` работает на per-run internal-сети, единственный достижимый
  адрес которой — egress-прокси с allowlist на один LLM-эндпоинт. `HTTP_PROXY` — это маршрут, а не
  разрешение: снять переменную и выйти в интернет нельзя, других маршрутов нет.
- **Агент не видит `benchmarks/`**: он получает checkout целевого репозитория, и ничего больше.
- Если у агента пустой diff — это валидный прогон, а не ошибка: пара останется в знаменателе как
  «без патча».
- Отсутствие образа у `mini`/`openhands` — это `HarnessError`, а не молчаливый пропуск: тег, который
  никто не выбирал, хуже честного «не настроено».

---

## 7. Проверка: `fixjury verify`

```bash
uv run fixjury verify --run 3f1c2b4d-8a1e-4c3b-9d2f-5e6a7b8c9d0e
```

Проверка идёт **не в контейнере агента**: берётся чистый `base_commit`, применяется сохранённый diff,
и в свежем контейнере запускаются:

1. `checks` задачи (`pytest`, `ruff`, `mypy` — из `task.yaml`),
2. policy-проверка (только `allowed_paths`, ничего `forbidden`),
3. held-out тесты из `benchmarks/heldout/<id>/` — монтируются read-only **только здесь**.

Вывод:

```
verdict run=3f1c2b4d-… automated=True tests=True lint=True types=True policy=True heldout=True tools={'pytest': '8.3.4', …}
```

Коды возврата: `0` — `automated_pass`, `1` — проверка не пройдена, `2` — ошибка (прогон не найден,
`--run` не UUID, не удалось собрать окружение проверки).

Важные свойства:

- **Идемпотентно**: повторный `verify` заменяет вердикт, а не добавляет второй. Это правильно для
  пересборки окружения и **опасно для уже отревьюенного прогона** — вердикт пересоздаётся без
  человеческих полей. Не перепроверяйте то, что уже принято человеком.
- Если окружение проверки не собралось, прогон помечается `infra_failed` с причиной
  `verification failed: …` — это исключение с записанной причиной, а не тихий пропуск. Успешный
  повторный `verify` снимает эту пометку.
- `--image` переопределяет образ песочницы, `--keep-workspace` оставляет каталог проверки.
- Прогон без diff получает вердикт с `automated_pass=false` и причиной «the run produced no diff».

---

## 8. Пакетный прогон: `fixjury bench`

```bash
uv run fixjury bench --agents baseline,mini --verify
uv run fixjury bench --agents baseline,mini --tasks T-001,T-002 --attempt 2
uv run fixjury bench --agents mini --max-seconds 7200
```

`bench` строит план «задача × агент» (задачи — из индекса корпуса, если `--tasks` пуст), ставит их в
Redis-очередь, сам разбирает очередь и, с `--verify`, проверяет каждый завершившийся прогон сразу.
Один прогон — одна транзакция; падение процесса не откатывает уже записанные прогоны.

| Опция | Смысл |
| --- | --- |
| `--agents` | список через запятую, обязательный |
| `--tasks` | подмножество задач; по умолчанию весь индекс |
| `--attempt` | номер попытки для всех пар |
| `--max-seconds` | бюджет: по исчерпании bench останавливается, а не «дожимает до утра» |
| `--verify` | проверять каждый `finished` прогон по ходу |
| `--image` | образ песочницы для всех прогонов |

Поведение:

- **Повторный запуск — это resume.** Пары, у которых уже есть прогон с тем же `attempt`, пропускаются
  (`skipped N pair(s) that already ran: …`), а не дублируются и не ломают команду.
- В начале bench возвращает в очередь «осиротевшие» прогоны из `processing` после падения.
- Итог: `bench done in …: finished=… failed=… verified=… budget_stop=… statuses=…`; код возврата 1,
  если есть незавершённые прогоны.
- `openhands` без образа и `mini` без образа — ошибка на старте: незачем ставить в очередь то, что
  невозможно исполнить.

> Для сетки 96 пар (32 задачи × 3 агента) нужны все три адаптера. В `.env.example` образы харнессов
> пустые: `mini` собирается по разделу 3.2, `openhands` требует своего образа и команды.

---

## 9. Очередь и воркер (когда bench не подходит)

`bench` сам себя обслуживает: очередь, воркер и проверка в одном процессе. Отдельные команды нужны,
когда прогоны должны идти в другом процессе или на другой машине.

```bash
uv run fixjury queue add --task T-001 --agent baseline
uv run fixjury queue status          # queued=… processing=…
uv run fixjury queue resume          # вернуть зависшие processing в queued
uv run fixjury worker                # разбирать очередь непрерывно
uv run fixjury worker --once         # ровно один прогон и выход
```

Воркер печатает строку на каждый прогон (`run <id> finished agent=… attempt=… diff=…B`) и не падает
на дубликате доставки — пишет `dropped a delivery: …` и продолжает. Если очередь пуста, `--once`
печатает `queue is empty`.

Пара (задача, агент, попытка) уникальна: `queue add` откажет на дубликате.

---

## 10. Слепое ревью

### 10.1 Упаковка батчей

```bash
uv run fixjury review pack --reviewer owner --pass 1
uv run fixjury review pack --tasks T-002,T-004 --min-runs 2
```

Батч — это прогоны **одной задачи** у разных агентов в сохранённом случайном порядке
(`review_batches.order_seed`), поэтому слепоту можно проверить задним числом. В батч попадают только
прогоны с `automated_pass = true`, у которых ещё нет человеческого вердикта; задач с меньшим числом
таких прогонов, чем `--min-runs` (минимум 2), команда не касается и в конце пишет
`nothing to pack: no task has enough accepted patches`, если не набралось ни одной.

Вывод — по строке на батч: `batch T-004-p1-1a2b3c4d5e6f task=T-004 patches=2`.

### 10.2 Страница ревью

```bash
uv run fixjury review serve --host 127.0.0.1 --port 8099
```

`http://127.0.0.1:8099/` — список батчей с прогрессом (`done / total`), `http://127.0.0.1:8099/batch/<id>`
— карточка одного патча.

Что видит ревьюер: описание issue, критерии приёмки из задачи, результаты проверок (`tests`, `lint`,
`types`, `policy`, `held-out`), сам патч, рубрику — и ничего больше. Ни агента, ни модели, ни
стоимости, ни времени в карточке нет; заголовки почты и строки `index …` из патча вырезаны.

Форма: `Accept` либо `Reject` + хотя бы одна причина из таксономии, и «секунды» на разбор.

| Код причины | Когда |
| --- | --- |
| `WRONG_ROOT_CAUSE` | лечится симптом, а не причина |
| `INCOMPLETE_FIX` | поведение из issue всё ещё воспроизводится |
| `EXTRA_FILES` | изменения вне разрешённых путей |
| `NO_REGRESSION_TEST` | нет теста, который падал бы на `base_commit` |
| `API_BREAK` | сломана публичная сигнатура |
| `SPEC_VIOLATION` | нарушено `forbidden` из задачи |
| `TEST_OVERFIT` | тест подогнан под фикс или ослаблен |
| `BUILD_BREAK` | сборка/запуск ломается |
| `UNREADABLE_DIFF` | патч невозможно применить или прочитать |
| `TIMEOUT_LIMIT` | упёрлось в лимит времени/шагов |

Правила записи: `reject` без причины — `400`; повторный вердикт по тому же прогону — `409`; после
записи страница переходит к следующему неотревьюенному патчу, в конце показывает `done`.
Вердикт помечается `blind = true` и получает `batch_id`; ревьюер в форме — `owner` (значение
захардкожено в шаблоне, `--reviewer` в `pack` помечает только батч).

### 10.3 Ограничения текущей реализации

- **Повторный проход (intra-rater) пока не поддержан.** `pack` берёт только прогоны без человеческого
  вердикта, поэтому упаковать второй проход (`--pass` с номером 2) не из чего: все пары уже имеют
  вердикт. По `PLAN.md` (§3.3, §11) повторная слепая приёмка 10 пар через ≥ 3 дня — часть метода; для
  неё нужен отдельный путь упаковки уже отревьюенных пар.
- **Батч требует ≥ 2 принятых автоматикой патчей на задачу.** Задача, где прошёл только один агент,
  в ревью не попадает и остаётся «неизвестной» парой: приёмка по ней не считается.

---

## 11. Метрики

```bash
uv run fixjury metrics compute --agent baseline --cost-basis list_price
uv run fixjury metrics compute --agent mini     --cost-basis list_price
uv run fixjury metrics show
```

`compute` читает все прогоны агента, джойнит их с вердиктами и пишет **снимок** в `metric_snapshots`
вместе с `config_hash` и `pricing_version`. Считать метрики где-то ещё (SQL, блокнот, страница,
голова) запрещено: отчёт и дашборд читают только снимки.

`--cost-basis` — `list_price` (токены × закреплённая ставка + секунды песочницы; справка, не счёт) или
`amortized` (доля подписки за окно прогона; реальные деньги). Базис печатается в таблице и в отчёте;
числа под разными `pricing_version` несравнимы.

`show` печатает по последнему снимку на агента, строками (пример; числа зависят от данных):

```
| mini | 100.0% | 20.7-100.0% | 100.0% | 31.2% | 1/1 | 10818016.00 | 3.4711 | 107.00 s | 0.0% |
| baseline | null | null | null | 18.8% | 0/0 | null | null | null | null |
```

`null` — нулевой знаменатель, а не ноль: пара, которую не смотрел ревьюер, «неизвестна». Здесь у
`baseline` ревью ещё не начато (`0/0`), поэтому приёмка, стоимость принятого фикса и медиана времени —
`null`, хотя успех автопроверок уже посчитан; у `mini` отревьюена одна пара, и приёмка 100% — это один
вердикт с очень широким интервалом, а не вывод о надёжности.

Колонки: агент · приёмка · 95% интервал Уилсона · приёмка без регрессий · успех автопроверок ·
принято/отревьюено · токенов на принятый фикс · стоимость принятого фикса · медиана времени ·
ложные завершения.

Если снимков нет — `no snapshots yet: run fixjury metrics compute first` и код 1.

---

## 12. Отчёт

```bash
uv run fixjury report                       # docs/report/README.md
uv run fixjury report --charts              # + acceptance.png, cost.png, regression-free.png, regressions.png
uv run fixjury report --basis amortized --out docs/report-amortized
uv run fixjury report --snapshot <uuid>     # воспроизвести отчёт по конкретному снимку
```

Отчёт собирается из последних снимков по каждому агенту (или из указанного), в таблицах — базис
стоимости, знаменатели и исключения. `--basis` только **маркирует** таблицы: сами числа берутся из
снимка, поэтому базис в отчёте должен совпадать с тем, под которым считали.

Если снимков нет — сообщение `no snapshots yet: run fixjury metrics compute first`, код 1.

---

## 13. Дашборд (статическая страница)

```bash
(cd web && npm ci)                          # один раз, Node 20.9+
uv run fixjury dashboard                    # docs/dashboard/index.html
uv run fixjury dashboard --out docs/site --basis amortized
```

`dashboard` пишет `snapshots.json` во временный каталог, запускает `npm run build` в `web/` с
`DASHBOARD_DATA` на этот файл и копирует статический экспорт из `web/out` в `--out`. То есть данные
попадают в страницу только этим путём: `npm run build` вручную соберёт страницу без снимков и упадёт.

Ошибки, которые команда объясняет сама: нет `web/package.json`; нет `npm` в `PATH`; сборка упала
(печатаются последние 15 строк вывода); нет `web/out/index.html`.

Страница читает поле `split` из снимка. Если `dashboard` падает с
`Cannot read properties of undefined (reading 'regression_free')`, значит в базе лежат снимки,
посчитанные более старой версией кода: пересчитайте метрики (`fixjury metrics compute --agent …` для
каждого агента) и повторите сборку.

Результат — статика: можно отдать через nginx, ничего не считает, только рисует.

---

## 14. Экспорт для публикации

```bash
uv run fixjury export                       # docs/export/
uv run fixjury export --out docs/export-2026-11
```

Пишет четыре файла:

| Файл | Что внутри |
| --- | --- |
| `runs.jsonl` | все колонки `runs`, **включая diff** — это доказательство |
| `verdicts.jsonl` | все колонки `verdicts`, включая человеческие поля |
| `rejections.jsonl` | автоматические и человеческие отказы с кодами таксономии |
| `manifest.json` | строк в каждом файле + `sha256` каждого файла + `verified: true` |

JSONL-файлы детерминированы: строки упорядочены, ключи отсортированы, кодировка фиксирована — два
экспорта одной базы дают побайтово одинаковые `*.jsonl` (в `manifest.json` различается только
`exported_at`). После записи каждый файл перечитывается с диска и сверяется по числу строк и хешу;
расхождение — ошибка, а не предупреждение.

Проверить копию руками:

```bash
cd docs/export && sha256sum runs.jsonl && grep -A2 runs.jsonl manifest.json
```

---

## 15. Где смотреть состояние

```bash
# сводка по прогонам
docker compose exec -T postgres psql -U fixjury -d fixjury \
  -c "select agent, status, count(*) from runs group by 1,2 order by 1,2;"

# сколько отревьюено и сколько батчей
docker compose exec -T postgres psql -U fixjury -d fixjury \
  -c "select count(*) as verdicts, count(human_verdict) as reviewed from verdicts;" \
  -c "select count(*) from review_batches;"

# очередь
uv run fixjury queue status

# мусор после падений
docker ps -a --filter "name=fixjury-run-" --format '{{.Names}}\t{{.Status}}'
docker network ls --filter "name=fixjury" --format '{{.Name}}'
```

`fixjury run`, `worker` и `bench` сами подчищают сети прогонов, оставшиеся от упавших процессов
(`reaped N stale run network(s)`). Контейнеры прогонов удаляются в `finally`; если процесс убили
`SIGKILL`, смотрите `docker ps -a`.

---

## 16. Диагностика

| Симптом | Причина | Что делать |
| --- | --- | --- |
| `mini has no pinned image; set the image and version before running it` | пустой `FIXJURY_MINI_IMAGE` | собрать образ (3.2) и заполнить `.env` |
| Прогон `infra_failed` без внятной причины, модель не отвечает | для opencode-go не задан `FIXJURY_LLM_SESSION` | заполнить заголовок `x-opencode-session` |
| `git clone failed: …` / долгий первый прогон | нет сети до github.com или пустой кэш зеркал | проверить сеть, задать `FIXJURY_CHECKOUT_CACHE` |
| `not a run id: '…'` | в `--run` передан не UUID | взять `id` из вывода `fixjury run` или из `runs` |
| `no snapshots yet: run fixjury metrics compute first` | снимков ещё нет | `fixjury metrics compute --agent <имя>` |
| Метрики показывают `null` в приёмке | ни одна пара не отревьюена | `review pack` → `review serve` → вердикты |
| `nothing to pack: no task has enough accepted patches` | у задач меньше двух `automated_pass` без вердикта | добить прогоны/проверки; см. 10.3 |
| `npm is not on PATH` / `the dashboard build failed` | нет Node 20.9+ или не сделан `npm ci` в `web/` | `(cd web && npm ci)`, затем повторить |
| `Cannot read properties of undefined (reading 'regression_free')` при сборке страницы | снимки в базе посчитаны старой версией кода, в них нет `split` | `fixjury metrics compute` по каждому агенту, затем повторить `dashboard` |
| Песочницы падают на сети | Docker Engine < 26 | обновить движок |
| Миграции: `type … already exists` после `alembic downgrade` | PostgreSQL-типы живут дольше таблиц | удалить типы явно (см. AGENTS.md, Pitfalls) |
| Страница ревью отдаёт 404 на батч | батч не создан или id устарел | `fixjury review pack` и открыть id из вывода |

---

## 17. Инварианты, которые нельзя ломать при эксплуатации

Полный список — в [`AGENTS.md`](../AGENTS.md); в обиходе важны шесть:

1. **Транспорт — только diff.** Никогда не проверяйте патч в контейнере агента и не доверяйте его
   рабочему каталогу.
2. **Held-out тесты не попадают агенту.** Они лежат в этом же репозитории, но монтируются только в
   контейнер проверки.
3. **Ревью слепое.** Агент и модель не должны появляться в карточке; порядок батча хранится, а не
   пересоздаётся.
4. **Метрики считаются один раз** — в `metrics/`, и читаются из снимков. Никакой арифметики в SQL,
   блокнотах и на странице.
5. **Замороженное не редактируется.** Правка `task.yaml` после первых прогонов инвалидирует сравнение;
   нужна новая версия задачи.
6. **Никакого auto-merge.** FixJury не пушит в целевой репозиторий и не открывает PR.

---

## 18. Проверки перед публикацией

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q
uv run fixjury tasks validate benchmarks --min-tasks 32
uv run fixjury metrics compute --agent baseline && uv run fixjury metrics compute --agent mini
uv run fixjury report --charts && uv run fixjury dashboard && uv run fixjury export
```

Перед выкладкой сверьте, что в отчёте и на странице одинаковые числа (оба читают один снимок) и что
базис стоимости в тексте совпадает с `--cost-basis`, под которым считали.
