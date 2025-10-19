from __future__ import annotations

from typing import Any, Dict, List, Optional
import re

import requests

from backend.core.config import settings
from backend.core import db


def _dialect_name() -> str:
    if settings.DB_DIALECT == "postgresql":
        return "PostgreSQL"
    if settings.DB_DIALECT == "sqlite":
        return "SQLite"
    return "SQL"


def _system_prompt() -> str:
    dialect = _dialect_name()
    return (
        f"Ты помощник для генерации SQL под {dialect}. Тебе даётся описание схемы и задача. "
        "Пиши только корректный SQL без пояснений. Возвращай один запрос SELECT/WITH. "
        "Разрешены только безопасные операции чтения."
    )


def build_schema_summary(tables: Optional[List[str]] = None, row_samples: int = 0) -> str:
    tables = tables or db.list_tables()
    lines: List[str] = [f"Схема БД ({_dialect_name()}):"]
    for t in tables:
        cols = db.get_table_columns(t)
        col_defs = ", ".join([f"{c['name']} {c['type']}" for c in cols])
        lines.append(f"- {t}({col_defs})")
        if row_samples:
            rows = db.fetch_sample(t, limit=row_samples)
            preview = rows[:3]
            lines.append(f"  примеры: {preview}")
    return "\n".join(lines)


def build_user_prompt(task: str, table_hint: Optional[str] = None) -> str:
    prefix = "Задача: " + task.strip()
    if table_hint:
        prefix += f". Приоритетная таблица: {table_hint}."
    if settings.DB_DIALECT == "postgresql":
        rules = (
            "Правила: используй двойные кавычки для имён, избегай небезопасных операций, не делай DELETE/UPDATE/INSERT; "
            "для топ-N используй ORDER BY и LIMIT; для частот: COUNT(*) AS cnt и GROUP BY."
        )
    else:
        rules = (
            "Правила: используй двойные кавычки для имён, избегай небезопасных операций, не делай DELETE/UPDATE/INSERT; "
            "для топ-N используй ORDER BY и LIMIT; для частот: COUNT(*) AS cnt и GROUP BY."
        )
    policy = (
        "\nПолитика безопасности: Разрешены SELECT/WITH; допускаются ALTER TABLE ... RENAME COLUMN и UPDATE С ОБЯЗАТЕЛЬНЫМ WHERE. "
        "Если нужно выполнить несколько запросов, разделяй их ';;'. Возвращай только SQL."
    )
    return prefix + "\n" + rules + policy


def generate_sql_from_nl(task: str, *, table_hint: Optional[str] = None) -> str:
    schema_text = build_schema_summary()
    user_prompt = build_user_prompt(task, table_hint=table_hint)

    # Yandex GPT: completion API
    headers = {
        "Authorization": f"Api-Key {settings.YANDEX_API_KEY}",
        "x-folder-id": settings.YANDEX_FOLDER_ID,
        "Content-Type": "application/json",
    }
    prompt_text = (
        _system_prompt() + "\n\n" + schema_text + "\n\n" + user_prompt + "\n\n"
        "Верни ТОЛЬКО SQL без объяснений и без тройных кавычек."
    )
    payload: Dict[str, Any] = {
        "modelUri": f"gpt://{settings.YANDEX_FOLDER_ID}/{settings.YANDEX_LLM_MODEL}/latest",
        "completionOptions": {"stream": False, "temperature": 0.0, "maxTokens": 800},
        "messages": [
            {"role": "user", "text": prompt_text},
        ],
    }

    resp = requests.post(settings.YANDEX_COMPLETION_URL, headers=headers, json=payload, timeout=90)
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        # Проброс подробностей ошибки от Yandex API
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise requests.HTTPError(f"{e} | details={detail}") from e
    data = resp.json()
    sql = (
        data.get("result", {})
        .get("alternatives", [{}])[0]
        .get("message", {})
        .get("text", "")
        .strip()
    )
    sql = _strip_code_fences(sql)
    return sql


def generate_plot_spec_from_nl(task: str, *, table_hint: Optional[str] = None) -> Dict[str, Any]:
    schema_text = build_schema_summary()
    base = (
        _system_prompt()
        + "\nСформируй спецификацию графика. Верни ТОЛЬКО JSON по схеме выше. Без комментариев и кода."
    )
    if table_hint:
        base += f"\nТаблица по умолчанию: {table_hint}."

    headers = {
        "Authorization": f"Api-Key {settings.YANDEX_API_KEY}",
        "x-folder-id": settings.YANDEX_FOLDER_ID,
        "Content-Type": "application/json",
    }
    prompt_text = base + "\n\n" + schema_text + "\n\nЗадача: " + task.strip()
    payload: Dict[str, Any] = {
        "modelUri": f"gpt://{settings.YANDEX_FOLDER_ID}/{settings.YANDEX_LLM_MODEL}/latest",
        "completionOptions": {"stream": False, "temperature": 0.0, "maxTokens": 800},
        "messages": [
            {"role": "user", "text": prompt_text},
        ],
    }
    resp = requests.post(settings.YANDEX_COMPLETION_URL, headers=headers, json=payload, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    text = (
        data.get("result", {})
        .get("alternatives", [{}])[0]
        .get("message", {})
        .get("text", "")
        .strip()
    )
    text = _strip_code_fences(text)
    import json
    try:
        spec = json.loads(text)
        if isinstance(spec, dict):
            return spec
    except Exception:
        pass
    # Фоллбек: пустая спецификация
    return {}


def generate_answer_from_rows(task: str, sql: str, rows: List[Dict[str, Any]], *, table_hint: Optional[str] = None) -> str:
    headers = {
        "Authorization": f"Api-Key {settings.YANDEX_API_KEY}",
        "x-folder-id": settings.YANDEX_FOLDER_ID,
        "Content-Type": "application/json",
    }
    preview_rows = rows[:50]
    columns = list(preview_rows[0].keys()) if preview_rows else []
    system_text = (
        "Ты аналитик данных. Дай человеко-понятный ответ на русском по результатам SQL. "
        "Формат: сначала краткий итог в одной фразе, затем краткие детали списком. "
        "Основание — только переданные строки результата SQL, без домыслов. "
        "Если данных нет — явно укажи это. Используй лаконичный Markdown без тройных кавычек."
    )
    user_text = (
        "Запрос пользователя: " + task.strip() + "\n"
        + "Выполненный SQL: " + sql.strip() + "\n"
        + "Колонки: " + ", ".join(columns) + "\n"
        + "Строки (превью): " + str(preview_rows)
    )
    payload: Dict[str, Any] = {
        "modelUri": f"gpt://{settings.YANDEX_FOLDER_ID}/{settings.YANDEX_LLM_MODEL}/latest",
        "completionOptions": {"stream": False, "temperature": 0.0, "maxTokens": 600},
        "messages": [
            {"role": "system", "text": system_text},
            {"role": "user", "text": user_text},
        ],
    }
    resp = requests.post(settings.YANDEX_COMPLETION_URL, headers=headers, json=payload, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    text = (
        data.get("result", {})
        .get("alternatives", [{}])[0]
        .get("message", {})
        .get("text", "")
        .strip()
    )
    return _strip_code_fences(text)


def _strip_code_fences(text: str) -> str:
    if not text:
        return text
    t = text.strip()
    t = t.replace("```sql", "```").replace("```SQL", "```").replace("```json", "```").replace("```JSON", "```")
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _pick_column(table: Optional[str], task: str, prefer_numeric: bool) -> Optional[str]:
    if not table:
        return None
    cols = db.get_table_columns(table)
    task_l = task.lower()
    # 1) по вхождению имени
    for c in cols:
        name = (c.get("name") or "").lower()
        if name and name in task_l:
            if prefer_numeric:
                t = str(c.get("type", "")).upper()
                if any(x in t for x in ["INT", "REAL", "FLOAT", "DOUBLE", "NUM", "DEC"]):
                    return c.get("name")
            else:
                return c.get("name")
    # 2) по типу
    if prefer_numeric:
        for c in cols:
            t = str(c.get("type", "")).upper()
            if any(x in t for x in ["INT", "REAL", "FLOAT", "DOUBLE", "NUM", "DEC"]):
                return c.get("name")
    else:
        for c in cols:
            t = str(c.get("type", "")).upper()
            if any(x in t for x in ["CHAR", "TEXT", "STRING", "UUID", "DATE", "TIME"]):
                return c.get("name")
    return cols[0].get("name") if cols else None


def heuristic_sql_from_nl(task: str, table_hint: Optional[str]) -> str:
    table = table_hint or (db.list_tables()[0] if db.list_tables() else None)
    task_l = task.lower()
    if any(k in task_l for k in ["средн", "average", "avg", "mean"]):
        col = _pick_column(table, task, prefer_numeric=True)
        return f'SELECT AVG("{col}") AS value FROM "{table}"' if table and col else 'SELECT 1 WHERE 1=2'
    if any(k in task_l for k in ["миним", "min"]):
        col = _pick_column(table, task, prefer_numeric=True)
        return f'SELECT MIN("{col}") AS value FROM "{table}"' if table and col else 'SELECT 1 WHERE 1=2'
    if any(k in task_l for k in ["макс", "max"]):
        col = _pick_column(table, task, prefer_numeric=True)
        return f'SELECT MAX("{col}") AS value FROM "{table}"' if table and col else 'SELECT 1 WHERE 1=2'
    if any(k in task_l for k in ["медиан", "median"]):
        col = _pick_column(table, task, prefer_numeric=True)
        if table and col:
            return (
                f'SELECT AVG(val) AS value FROM ('
                f' SELECT "{col}" AS val FROM "{table}" ORDER BY val '
                f' LIMIT 2 - (SELECT COUNT(*) FROM "{table}") % 2 '
                f' OFFSET (SELECT (COUNT(*) - 1) / 2 FROM "{table}")'
                f')'
            )
        return 'SELECT 1 WHERE 1=2'
    if any(k in task_l for k in ["частот", "часто", "топ", "top", "count", "value_counts"]):
        col = _pick_column(table, task, prefer_numeric=False)
        return f'SELECT "{col}" AS value, COUNT(*) AS cnt FROM "{table}" GROUP BY "{col}" ORDER BY cnt DESC LIMIT 50' if table and col else 'SELECT 1 WHERE 1=2'
    # default: first 20
    return f'SELECT * FROM "{table}" LIMIT 20' if table else 'SELECT 1 WHERE 1=2'


def simple_answer_from_rows(task: str, sql: str, rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "Данных нет по данному запросу."
    keys = list(rows[0].keys())
    if len(keys) == 1 or (len(keys) == 1 and keys[0].lower() in ("value",)):
        v = rows[0][keys[0]]
        return f"Ответ: {v}"
    if set([k.lower() for k in keys]).issuperset({"value", "cnt"}):
        top = rows[:3]
        pairs = ", ".join([f"{r.get('value')}: {r.get('cnt')}" for r in top])
        return f"Топ: {pairs}"
    return f"Найдено строк: {len(rows)}"


