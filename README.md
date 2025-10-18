# LLM Tools: интерактивная работа с Excel/CSV через SQL и YandexGPT

Прототип, который даёт LLM «инструменты» для работы с табличными данными:
- загрузка CSV/XLSX в SQL (SQLite по умолчанию, Postgres — опционально),
- просмотр таблиц и сэмплов,
- NL → SQL (генерация и выполнение запросов через YandexGPT),
- минимальный веб‑интерфейс на Streamlit (поле запроса + таблица + авто‑графики по числовым колонкам).

Цель — обойти ограничение контекста LLM: данные не «скармливаются» модели, а лежат в БД; модель видит лишь краткую сводку схемы и генерирует SQL, который исполняет бэкенд.

## Возможности
- Загрузка CSV/XLSX → SQL (создание/замена/добавление в таблицы).
- Просмотр списка таблиц, колонок, количества строк, сэмпла (API).
- NL → SQL (SELECT/WITH) и безопасные изменения (переименование колонки, UPDATE с обязательным WHERE).
- Один экран Streamlit:
  - загрузка файла,
  - строка запроса (NL или чистый SELECT/WITH),
  - таблица (всегда показывается: результат запроса или sample текущей таблицы),
  - автографики по числовым колонкам (линейная оценка плотности KDE).

## Архитектура
- Backend: FastAPI
  - `/api/v1/ingest/upload` — загрузка CSV/XLSX
  - `/api/v1/schema/*` — таблицы/колонки/счётчик/сэмпл
  - `/api/v1/query/sql` — безопасный SELECT/WITH
  - `/api/v1/llm/nlquery`, `/api/v1/llm/execute`, `/api/v1/llm/nlquery_multi` — NL→SQL и мультизапросы
  - `/api/v1/plots/nlplot` — LLM‑спека графика → подготовленные данные (есть фоллбек)
- LLM: YandexGPT (completion API)
- DB: SQLite по умолчанию (Postgres опционально)
- Frontend: Streamlit `frontend/App.py` (минимальный одноэкранный UI)

## Установка
1) Python 3.10+ (рекоменд. 3.11)
2) Установите зависимости:
```bash
pip install -r requirements.txt
```

## Конфигурация (.env)
Обязательные/ключевые переменные:
```
# Бэкенд (локально)
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000
# Для публичного UI укажите публичный URL API (например, от cloudflared)
# BACKEND_BASE_URL=https://<public-api-url>

# БД (по умолчанию SQLite)
DATABASE_URL=sqlite:///./data/app.db
# Для Postgres:
# DATABASE_URL=postgresql://<user>:<pass>@<host>:<port>/<db>

# Yandex GPT
LLM_PROVIDER=yandex
YANDEX_API_KEY=<ваш_api_key>
YANDEX_FOLDER_ID=<ваш_folder_id>
YANDEX_LLM_MODEL=yandexgpt-lite
YANDEX_COMPLETION_URL=https://llm.api.cloud.yandex.net/foundationModels/v1/completion

# CORS
CORS_ORIGINS=*
```

## Запуск локально
В одном терминале:
```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```
В другом терминале:
```bash
streamlit run frontend/App.py --server.port 8501
```
Откройте UI: http://localhost:8501

## Использование UI
1) Загрузите CSV/XLSX (после загрузки UI фиксируется на новой таблице).
2) Введите запрос:
   - NL: «Переименуй колонку age в age_years и выведи 10 строк из таблицы my_table».
   - SQL: `SELECT * FROM "my_table" LIMIT 10`.
3) Таблица всегда видна (результат запроса или sample текущей таблицы).
4) Автографики по числовым колонкам строятся автоматически (KDE‑кривая).

## Политика безопасности SQL
- Разрешены чтение: SELECT/WITH.
- Разрешены безопасные write‑операции в `/llm/execute`:
  - `ALTER TABLE ... RENAME COLUMN`
  - `UPDATE ... WHERE ...` (обязательно наличие WHERE).
- Несколько выражений разделяются `;;`. Бэкенд разбивает и исполняет последовательно. Опасные или неподдерживаемые операции отбрасываются.

## Как решается проблема ограниченного контекста LLM
- В БД хранятся данные; LLM не видит полный датасет.
- Модель получает компактную сводку схемы (названия таблиц и колонок, типы, опционально несколько примеров).
- Пользовательский запрос → YandexGPT генерирует SQL → бэкенд исполняет → возвращает только результат.
- Для графиков LLM возвращает JSON‑спецификацию (тип графика, оси/агрегаты), бэкенд готовит агрегированные данные. Есть фоллбек на эвристику.

## Структура проекта
```
backend/
  main.py
  core/
    config.py
    db.py
  api/
    routes.py
    v1/
      ingest.py
      schema.py
      query.py
      llm.py
      plots.py
  services/
    ingest_service.py
    llm_service.py
    plot_service.py
frontend/
  App.py
.env.example
requirements.txt
```

## API шпаргалка
- Загрузка:
  - `POST /api/v1/ingest/upload` (multipart form: `file`, `table_name?`, `if_exists=replace|append|fail`)
- Cхема:
  - `GET /api/v1/schema/tables`
  - `GET /api/v1/schema/tables/{table}/columns`
  - `GET /api/v1/schema/tables/{table}/count`
  - `GET /api/v1/schema/tables/{table}/sample?limit=50`
  - `POST /api/v1/schema/tables/{table}/rename?old=...&new=...`
- SQL:
  - `POST /api/v1/query/sql` — только SELECT/WITH
  - `POST /api/v1/query/sql_batch` — параллельные SELECT
- LLM:
  - `POST /api/v1/llm/nlquery` — NL → один SELECT
  - `POST /api/v1/llm/execute` — NL → несколько выражений (SELECT/WITH + безопасные write)
  - `POST /api/v1/llm/nlquery_multi` — NL → несколько SELECT (разделитель `;;`)
- Графики:
  - `POST /api/v1/plots/nlplot` — NL → JSON‑спека графика → данные (с фоллбеком)



## Примечания
- UI всегда показывает таблицу: если NL‑запрос дал write‑операцию без читающего результата, отобразится sample текущей таблицы.
- Для Postgres укажите `DATABASE_URL=postgresql://...` и установите `psycopg2-binary`.

