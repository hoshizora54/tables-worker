from __future__ import annotations

from typing import Any, Dict, List, Optional

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
    if sql.startswith("```") and sql.endswith("```"):
        sql = sql.strip("`")
    sql = sql.replace("```sql", "").replace("```SQL", "").replace("```", "").strip()
    return sql


def generate_plot_spec_from_nl(task: str, *, table_hint: Optional[str] = None) -> Dict[str, Any]:
    """
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
    text = text.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
    import json
    try:
        spec = json.loads(text)
        if isinstance(spec, dict):
            return spec
    except Exception:
        pass
    # Фоллбек: пустая спецификация
    return {}


