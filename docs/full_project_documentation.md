## Агент для анализа данных с Tool Use

Система интерактивного анализа табличных данных (CSV/XLSX) на естественном языке с использованием агентской архитектуры и Tool Use. Агент автоматически выбирает и применяет инструменты для выполнения запросов пользователя.

---

## Основные возможности

- **Агентская система с Tool Use**: LLM самостоятельно выбирает и вызывает инструменты на основе запросов на естественном языке.
- **Загрузка данных**: CSV/XLSX → SQL (SQLite по умолчанию, PostgreSQL опционально).
- **Интерактивный анализ**: Единое поле ввода для запросов на естественном языке.
- **Статистический анализ**: t‑тесты, ANOVA, скользящие средние, группировки, сводные таблицы.
- **Визуализация**: Автоматическое построение графиков (гистограммы, столбчатые, линейные, scatter).
- **Версионирование**: Откат состояний таблиц и экспорт версий.
- **Безопасность**: Ограничение на опасные SQL‑операции и строгие правила для SQLite.
- **Понятные ответы**: Финальный ответ — развернутый и человеко‑понятный (итог, смысл, ключевые цифры, рекомендации).

---

## Архитектура

### Общий обзор

- **Backend (FastAPI)**:
  - Агент, инструменты (Tools), работа с БД, версионирование.
- **Frontend‑Vue (SPA)**:
  - Основной UI под агентский эндпоинт `/api/v1/llm/agent`.
- **Frontend‑Streamlit**:
  - Альтернативный, более «консольный» интерфейс для NL/SQL‑запросов.
- **База данных**:
  - SQLite по умолчанию (`data/app.db`), PostgreSQL при указании `DATABASE_URL`.
- **Бенчмаркинг**:
  - Скрипт `scripts/run_benchmark.py` + стратегия в `docs/benchmark_strategy.md`.

### Структура проекта

```text
.
├── backend/
│   ├── main.py                 # Точка входа FastAPI
│   ├── api/
│   │   ├── routes.py           # Маршрутизация API
│   │   └── v1/
│   │       ├── llm.py          # LLM и агентские эндпоинты
│   │       ├── ingest.py       # Загрузка/экспорт файлов
│   │       ├── schema.py       # Работа со схемой БД
│   │       ├── query.py        # SQL-запросы (SELECT/WITH)
│   │       ├── plots.py        # Генерация данных и рендеринг графиков
│   │       └── versioning.py   # Версионирование таблиц
│   ├── services/
│   │   ├── agent_service.py    # Агентский цикл и выбор инструментов
│   │   ├── tool_service.py     # Определение и вызов инструментов (Tools)
│   │   ├── llm_service.py      # Интеграция с YandexGPT (SQL, ответы, графики)
│   │   ├── plot_service.py     # Рендеринг графиков (matplotlib + seaborn)
│   │   ├── ingest_service.py   # Сохранение DataFrame в БД
│   │   └── versioning_service.py # Механизм версий таблиц
│   └── core/
│       ├── config.py           # Конфигурация и .env
│       └── db.py               # Подключение к БД и утилиты (run_select и т.д.)
├── frontend-vue/
│   ├── src/
│   │   ├── App.vue             # Основной SPA-интерфейс
│   │   ├── api.ts              # Обёртки над REST API
│   │   └── main.ts             # Точка входа Vue
│   └── package.json
├── frontend/
│   └── App.py                  # Streamlit UI
├── data/                       # SQLite БД, примеры данных, результаты бенчмарка
├── docs/
│   ├── benchmark_strategy.md   # Стратегия бенчмаркинга
│   └── full_project_documentation.md # (этот файл)
├── scripts/
│   ├── run_benchmark.py        # Запуск бенчмарков
│   └── README_BENCHMARK.md
├── requirements.txt
└── README.md
```

---

## Конфигурация и переменные окружения

### Основные переменные (.env)

- **База данных**
  - **`DATABASE_URL`**:
    - По умолчанию: `sqlite:///./data/app.db`.
    - Пример PostgreSQL: `postgresql://user:pass@host:port/db`.
  - **`POSTGRES_HOST`**, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`  
    Используются, если `DATABASE_URL` пустой или начинается с `postgresql://auto`.

- **Режим агента**
  - **`LLM_ONLY_SQL`**: `0` или `1`
    - `0` — полный режим: доступны все инструменты (`sql_query`, `t_test`, `anova`, `moving_average`, `groupby`, `pivot`, `join`, `plot`, `filter`).
    - `1` — агент может использовать только `sql_query`, вся аналитика через SQL.

- **LLM / YandexGPT**
  - **`LLM_PROVIDER`** — по умолчанию `yandex`.
  - **`YANDEX_API_KEY`**
  - **`YANDEX_FOLDER_ID`**
  - **`YANDEX_LLM_MODEL`** — по умолчанию `yandexgpt-lite`.
  - **`YANDEX_COMPLETION_URL`** — по умолчанию `https://llm.api.cloud.yandex.net/foundationModels/v1/completion`.

- **CORS**
  - **`CORS_ORIGINS`** — `*` либо список доменов через запятую.

- **Frontend / Streamlit**
  - **`BACKEND_BASE_URL`** — полный URL бэкенда (если задан, имеет приоритет).
  - **`BACKEND_HOST`**, `BACKEND_PORT` — используются, если `BACKEND_BASE_URL` не задан.

- **Frontend‑Vue**
  - В `.env` в папке `frontend-vue`:
    - **`VITE_API_URL`** — базовый URL API, по умолчанию `http://localhost:8000/api`.

---

## Бэкенд

### Создание приложения и CORS (`backend/main.py`)

- **`create_app()`**:
  - Создаёт `FastAPI`‑приложение с заголовком «LLM Tools for Excel/CSV → SQL».
  - Настраивает CORS:
    - Если `CORS_ORIGINS="*"` → полный доступ.
    - Иначе — список доменов из переменной.
  - Регистрирует:
    - `GET /health` — проверка работоспособности.
    - Все API‑роуты под префиксом `/api`.
  - Инициализирует схему версий (`init_versioning_schema()`); ошибки не блокируют запуск.

### Доступ к БД (`backend/core/db.py`)

- **Основные функции**:
  - **`get_engine()`** — создание и кеш SQLAlchemy `Engine` с учётом SQLite (`check_same_thread=False`).
  - **`list_tables()`** — список таблиц.
  - **`get_table_columns(table)`** — метаданные колонок (имя, тип, nullable, default).
  - **`get_row_count(table)`** — количество строк.
  - **`fetch_sample(table, limit)`** — превью строк.
  - **`run_select(sql, params)`** — выполнение SELECT/WITH, возврат списка словарей.
  - **`run_execute(sql, params)`**, **`run_execute_rowcount(sql, params)`** — выполнение DDL/DML с транзакцией и `rowcount`.

---

## API: загрузка и экспорт таблиц (`/api/v1/ingest`)

### Загрузка файлов (`POST /api/v1/ingest/upload`)

- **Параметры**:
  - `file` — CSV или XLSX (multipart/form-data).
  - `table_name` (Form, опционально) — имя таблицы.
  - `if_exists` (Form, по умолчанию `"replace"`) — `"replace" | "append" | "fail"`.

- **Поведение**:
  - CSV → `pd.read_csv`, XLSX → `pd.read_excel`.
  - Имя таблицы — `table_name` или имя файла без расширения, затем санитизация.
  - Сохранение через `df.to_sql(..., if_exists=...)`.
  - Если есть колонка `id`, создаётся индекс `idx_<table>_id`.
  - Создаётся снепшот версии (`operation="upload"`, `details="if_exists=..."`).

- **Ответ**:

```json
{
  "status": "ok",
  "table": "actual_table_name",
  "rows": 1234
}
```

### Экспорт таблиц

- **`GET /api/v1/ingest/export/csv?table=<name>`** — отдаёт `table.csv`.
- **`GET /api/v1/ingest/export/xlsx?table=<name>`** — отдаёт `table.xlsx`.

---

## API: работа со схемой БД (`/api/v1/schema`)

- **`GET /api/v1/schema/tables`**  
  Возвращает список таблиц: `{"tables": ["t1", "t2", ...]}`.

- **`GET /api/v1/schema/tables/{table}/columns`**  
  Метаданные колонок: имя, тип, nullable, default.

- **`GET /api/v1/schema/tables/{table}/count`**  
  Количество строк.

- **`GET /api/v1/schema/tables/{table}/sample?limit=N`**  
  Превью строк: `{"rows": [...]}`.

- **`POST /api/v1/schema/tables/{table}/rename?old=...&new=...`**  
  Переименование колонки:
  - Выполняет `ALTER TABLE "table" RENAME COLUMN "old" TO "new"`.
  - Создаёт версию (`operation="rename_column"`).

---

## API: SQL‑запросы (`/api/v1/query`)

### Один запрос (`POST /api/v1/query/sql`)

- **Тело**:

```json
{
  "sql": "SELECT ...",
  "params": { "param": "value" }
}
```

- **Ограничения**:
  - Запрос должен начинаться с `SELECT` или `WITH`.
- **Ответ**:

```json
{
  "rows": [...],
  "count": 42
}
```

### Пакет запросов (`POST /api/v1/query/sql_batch`)

- **Тело**:

```json
{
  "items": [
    { "sql": "SELECT ...", "params": { } },
    { "sql": "WITH ...", "params": { } }
  ],
  "max_workers": 4
}
```

- Все запросы должны быть `SELECT`/`WITH`.
- Выполняются параллельно (ThreadPoolExecutor).
- **Ответ**:

```json
{
  "results": [
    { "rows": [...], "count": 10 },
    { "error": "Сообщение об ошибке" }
  ]
}
```

---

## Визуализация (`/api/v1/plots`)

### Генерация данных по NL‑запросу (`POST /api/v1/plots/nlplot`)

- **Тело**:

```json
{
  "task": "Построй гистограмму распределения возраста",
  "table_hint": "users"
}
```

- Логика:
  - Через LLM строится спецификация графика (тип, таблица, x, y, agg, bins).
  - Если LLM вернула неполную/невалидную спеку — используются эвристики:
    - Выбор таблицы по `table_hint`/списку таблиц.
    - Автоматический выбор колонок `x`/`y` по тексту запроса и типам данных.
  - Для bar/line:
    - Строится `SELECT x, <AGG(y)> AS y ... GROUP BY x ORDER BY y DESC LIMIT N`.
  - Для hist:
    - Выбирается числовая колонка `x`, возвращаются «сырые» значения (биннинг на клиенте).

- **Ответ**:

```json
{
  "spec": { "type": "hist", "table": "users", "x": "age", "bins": 20 },
  "rows": [...],
  "bins": 20
}
```

### Рендеринг графика (`POST /api/v1/plots/render`)

- **Тело**:

```json
{
  "type": "bar|line|hist|scatter",
  "table": "users",
  "x": "age",
  "y": "income",
  "agg": "mean",
  "limit": 100,
  "bins": 20,
  "where": "age > 18",
  "title": "..."
}
```

- Строится SQL‑запрос, формируется DataFrame, затем график (matplotlib + seaborn) кодируется в base64.

- **Ответ**:

```json
{
  "image_base64": "...",
  "rows": [...],
  "type": "bar"
}
```

---

## LLM и агент (`/api/v1/llm`)

### NL→SQL (`POST /api/v1/llm/nl2sql`)

- **Тело**:

```json
{
  "task": "Посчитай средние продажи по категориям",
  "table_hint": "sales"
}
```

- Строится подробный промпт с:
  - Описанием схемы БД (таблицы, колонки, примеры строк).
  - Правилами для SQLite/PostgreSQL (двойные кавычки вокруг идентификаторов, отсутствие `DATE_FORMAT/DATE_TRUNC/EXTRACT/TO_CHAR` и т.п.).
- **Ответ**:

```json
{
  "sql": "SELECT ..."
}
```

### NL‑запрос с выполнением (`POST /api/v1/llm/nlquery`)

- **Тело**:

```json
{
  "task": "Покажи топ-5 категорий по продажам",
  "table_hint": "sales"
}
```

- Внутри:
  - Генерация SQL через `generate_sql_from_nl`.
  - Выполнение `run_select(sql)`.
  - Попытка получить человеко‑понятное объяснение через `generate_answer_from_rows`.

- **Ответ**:

```json
{
  "sql": "SELECT ...",
  "rows": [...],
  "count": 5,
  "answer": "Короткий итог: ...\nЧто это значит: ...\nКлючевые цифры: ...\nРекомендации: ...\nОграничения: ..."
}
```

### Множественные SELECT’ы (`POST /api/v1/llm/nlquery_multi`)

- LLM может вернуть несколько запросов, разделённых `;;` и `;`.
- Сервис:
  - Разбивает SQL‑блок.
  - Отбрасывает не‑SELECT/WITH.
  - Для каждого делает `run_select` и собирает результаты/ошибки.

### Выполнение последовательности SQL + безопасные write (`POST /api/v1/llm/execute`)

- Поддерживает:
  - `SELECT`/`WITH` для чтения.
  - **Безопасные записи**:
    - `ALTER TABLE ... RENAME COLUMN ...`
    - `UPDATE ... WHERE ...`
- Внутри:
  - SQL блок разбивается на отдельные выражения.
  - Для `read` — `run_select`.
  - Для `write` — `run_execute_rowcount` + попытка определить имя таблицы и сделать снепшот (`operation="llm_write"`).

- **Ответ**:

```json
{
  "results": [
    { "type": "read", "sql": "...", "rows": [...], "count": 10 },
    { "type": "write", "sql": "...", "rowcount": 5 }
  ],
  "raw": "исходный SQL-блок"
}
```

---

## Агентский эндпоинт (`POST /api/v1/llm/agent`)

### Формат запроса

```json
{
  "task": "Построй гистограмму распределения возраста",
  "table_hint": "users"
}
```

### Формат ответа (упрощённый пример)

```json
{
  "tool_calls": [
    { "tool_name": "sql_query", "parameters": { "sql": "SELECT ..." } },
    { "tool_name": "plot", "parameters": { "type": "hist", "table": "users", "x": "age" } }
  ],
  "results": [
    {
      "tool_name": "sql_query",
      "parameters": { ... },
      "result": { "rows": [...], "count": 1000 }
    },
    {
      "tool_name": "plot",
      "parameters": { ... },
      "result": { "image_base64": "...", "rows": [...], "type": "hist" }
    }
  ],
  "final_answer": "Короткий итог: ...\nЧто это значит: ...\nКлючевые цифры: ...\nРекомендации: ...\nОграничения: ..."
}
```

### Логика агентского цикла

- **Подготовка контекста**:
  - Схема БД (таблицы/колонки/примеры строк).
  - Полное описание всех инструментов (`sql_query`, `t_test`, `anova`, `moving_average`, `groupby`, `pivot`, `join`, `plot`, `filter`).
  - Инструкции:
    - Использовать только реальные имена таблиц/колонок.
    - Всегда оборачивать идентификаторы в двойные кавычки.
    - Для дат в SQLite не использовать `DATE_TRUNC/DATE_FORMAT/EXTRACT/TO_CHAR`.
    - Для t‑test/ANOVA выбирать корректные группы и числовые колонки.
    - В нормальном режиме после получения данных через `sql_query` переходить к аналитике (t_test/anova/plot/groupby).
    - В режиме `LLM_ONLY_SQL` использовать только `sql_query` и делать всё через SQL.

- **Итерации**:
  - LLM на каждом шаге возвращает JSON `{ "tool_name": "...", "parameters": {...} }`.
  - JSON может быть в Markdown‑блоке — парсится регуляркой.
  - Агент вызывает `call_tool`, собирает результаты и кратко описывает их в истории диалога.

- **Защита от ошибок и дубликатов**:
  - Если LLM повторяет тот же `sql_query`, агент подсказывает перейти к аналитике/следующему SQL‑шагу.
  - Ошибки инструментов логируются, но **не** попадают в `results`; вместо этого LLM получает подробные подсказки по исправлению SQL (имен таблиц/колонок, правила SQLite).
  - После нескольких ошибок подряд агент завершает цикл и строит финальный ответ по имеющимся результатам.

- **Финальный ответ**:
  - Структура:
    - Короткий итог.
    - Что это значит.
    - Ключевые цифры.
    - Рекомендации.
    - Ограничения.
  - Используется детерминированная статистика по последним табличным результатам (топ‑N, min/max/mean, квантили, сравнение двух числовых столбцов и т.п.), чтобы избежать «выдуманных» чисел.

---

## Инструменты (Tools)

### Общий список

- **`sql_query`** — выполнение безопасных SELECT/WITH запросов.
- **`t_test`** — t‑тест (две группы или режим попарных сравнений через `groups`).
- **`anova`** — однофакторный ANOVA.
- **`moving_average`** — скользящее среднее по временному ряду.
- **`groupby`** — группировка с агрегатами (sum/avg/min/max/count).
- **`pivot`** — сводные таблицы (pivot table) на базе pandas.
- **`join`** — объединение двух таблиц (INNER/LEFT JOIN) в новую таблицу.
- **`plot`** — построение графиков (bar/line/hist/scatter) и возврат base64‑PNG.
- **`filter`** — фильтрация строк по условию (WHERE/ORDER BY/LIMIT).

В режиме `LLM_ONLY_SQL=1` агенту доступен только инструмент **`sql_query`**.

---

## Версионирование таблиц

### Внутренняя реализация

- Служебная таблица версий: `__table_versions__`:
  - `table_name`, `version_num`, `created_at`, `operation`, `details`, `snapshot_table`.
- **Снепшоты**:
  - При загрузке данных, join, переименовании колонок и безопасных LLM‑записях создаётся новая физическая таблица `<имя>__v<номер>` и запись в `__table_versions__`.

### API (`/api/v1/versioning`)

- **`GET /api/v1/versioning/versions/{table}`**  
  Список версий таблицы.

- **`POST /api/v1/versioning/versions/{table}/restore/{version}`**  
  Полный откат таблицы к конкретной версии (через переименование и пересоздание).

- **`GET /api/v1/versioning/versions/{table}/{version}/export/csv`**  
  Экспорт конкретной версии таблицы в CSV.

- **`GET /api/v1/versioning/versions/{table}/{version}/export/xlsx`**  
  Экспорт версии в XLSX.

---

## Фронтенд‑Vue (основной UI)

### Основные сценарии

- **Загрузка файлов**:
  - `uploadFile(file)` → `POST /api/v1/ingest/upload`.
  - Обновляет список таблиц, показывает превью (`sampleTable`).

- **Управление таблицами**:
  - Селектор таблиц (`listTables`).
  - Превью через `sampleTable(table, limit)`.
  - Экспорт через `exportCsv(table)`.
  - История версий (`listVersions`), откат (`restoreVersion`), экспорт конкретной версии (`exportCsvVersion`).

- **Агентский запрос**:
  - Поле `v-model="agentQueryText"`, кнопка «Выполнить».
  - Вызов `agentQuery(task, table_hint?)` → `/api/v1/llm/agent`.
  - Отображается:
    - Финальный ответ (`final_answer`).
    - Список вызванных инструментов и их краткий статус.
    - График (`image_base64`) при наличии.
    - Таблицы результатов инструментов (`rows` → HTML‑таблица).

### API‑клиент (`frontend-vue/src/api.ts`)

- **Базовый URL**: `BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api'`.
- **Функции**:
  - **Данные**: `uploadFile`, `listTables`, `sampleTable`.
  - **Графики**: `renderPlot`.
  - **Версионирование**: `listVersions`, `restoreVersion`, `exportCsv`, `exportCsvVersion`.
  - **LLM/агент**: `nl2sql`, `nlquery`, `sqlQuery`, `agentQuery`.

---

## Фронтенд‑Streamlit (альтернативный UI)

### Возможности

- **Загрузка файла**:
  - Компонент `st.file_uploader`, кнопка «Загрузить`.
  - Отправляет файл на `/api/v1/ingest/upload`, сохраняет имя таблицы в `st.session_state["current_table"]`.

- **Поле «Запрос»**:
  - Если строка начинается с `SELECT` или `WITH`:
    - Делает `POST /api/v1/query/sql`.
  - Иначе:
    - Делает `POST /api/v1/llm/nlquery` с `table_hint`.

- **Отображение результатов**:
  - Таблица (`st.dataframe`) с результатами или превью первой таблицы, если результата запроса нет.
  - Для NL‑запросов:
    - Показывает `answer` (Markdown) и SQL, который был выполнен (в экспандере).

- **Визуализация**:
  - По ключевым словам (`график`, `диаграм`, `hist`, `гист`, `распределение`, `top`, `частоты` и т.п.) вызывает `/api/v1/plots/nlplot`.
  - Строит KDE‑график через Altair по числовой оси X.
  - Дополнительно пытается автоматически построить KDE по первой подходящей числовой колонке результата/превью.

---

## Установка и запуск

### Установка зависимостей

- **Python**:

```bash
pip install -r requirements.txt
```

- **Frontend‑Vue**:

```bash
cd frontend-vue
npm install
```

### Настройка `.env`

Пример:

```env
# Бэкенд
DATABASE_URL=sqlite:///./data/app.db

# Яндекс GPT
LLM_PROVIDER=yandex
YANDEX_API_KEY=ВАШ_API_KEY
YANDEX_FOLDER_ID=ВАШ_FOLDER_ID
YANDEX_LLM_MODEL=yandexgpt-lite
YANDEX_COMPLETION_URL=https://llm.api.cloud.yandex.net/foundationModels/v1/completion

# CORS
CORS_ORIGINS=*

# Режим работы агента
LLM_ONLY_SQL=0
```

### Запуск

- **Бэкенд**:

```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

- **Frontend‑Vue**:

```bash
cd frontend-vue
npm run dev
# Открыть http://localhost:5173
```

- **Streamlit UI (опционально)**:

```bash
cd frontend
streamlit run App.py
# Открыть http://localhost:8501
```

---

## Использование

### Агентский сценарий (рекомендуемый)

1. Загрузите CSV/XLSX через Vue‑интерфейс.
2. Выберите таблицу в боковой панели.
3. Введите запрос на естественном языке, например:
   - «Построй гистограмму распределения возраста».
   - «Сравни продажи в группах A и B с помощью t‑теста».
   - «Создай сводную таблицу по регионам и продуктам».
4. Нажмите «Выполнить».
5. Посмотрите:
   - Итоговый текстовый ответ.
   - Табличные результаты.
   - Графики (если применимо).

### Прямые SQL и NL‑подсказки (Streamlit)

1. Загрузите файл.
2. В поле «Запрос» вводите:
   - Либо SQL (`SELECT`/`WITH`).
   - Либо NL‑запросы (как в примерах выше).
3. Получайте ответы и при необходимости смотрите сгенерированный SQL.

---

## Бенчмаркинг

### Скрипт и зависимости

- Скрипт: `scripts/run_benchmark.py`.
- Дополнительная зависимость: `datasets` (уже указана в `requirements.txt`).

### Запуск

```bash
# Бэкенд:
uvicorn backend.main:app --host 127.0.0.1 --port 8000

# Бенчмарк:
python scripts/run_benchmark.py --dataset sql-create-context --limit 20
```

### Поддерживаемые бенчмарки

- **`sql-create-context`** — рекомендованный Text‑to‑SQL бенчмарк.
- **`spider`**
- **`wikisql`**

### Метрики

- **Execution Accuracy** — точность выполнения SQL.
- **SQL Usage Rate** — доля кейсов, где использован инструмент `sql_query`.
- **Среднее время выполнения** и **95‑й перцентиль** времени.

Результаты сохраняются в `data/benchmark/benchmark_results.json` (вопрос, ожидаемый SQL, фактический SQL, флаг корректности, время).

---

## Расширение проекта

### Добавление нового инструмента (Tool)

- **Шаг 1**: в `backend/services/tool_service.py`:
  - Добавить новый `ToolDefinition` в `get_tools_definitions()`.
  - Добавить реализацию в `call_tool()`.

- **Шаг 2**: при необходимости:
  - Добавить отдельный UI‑элемент на фронтенде или просто позволить агенту использовать новый инструмент через NL‑инструкции.

- **Шаг 3**: перезапустить бэкенд — новый инструмент станет доступен агенту автоматически.

### Расширение визуализации

- Добавить поддержку типа графика в:
  - `backend/services/plot_service.py` (рендеринг).
  - `backend/api/v1/plots.py` и ветку `plot` в `tool_service.py`.
  - При необходимости — в компоненты фронтенда (Vue/Streamlit).

---


