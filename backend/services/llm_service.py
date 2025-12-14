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
    if dialect == "SQLite":
        return (
            "Ты помощник для генерации SQL под SQLite. Тебе даётся описание схемы и задача. "
            "Пиши только корректный SQL без пояснений. Возвращай один запрос SELECT/WITH. "
            "Разрешены только безопасные операции чтения.\n"
            "Правила для SQLite:\n"
            "- Всегда заключай имена таблиц и колонок в двойные кавычки. Пример: FROM \"1810_2\", SELECT \"Откр.\", \"Дата\".\n"
            "- Не используй функции других СУБД: DATE_FORMAT, DATE_TRUNC, EXTRACT, TO_CHAR — их нет в SQLite.\n"
            "- Для группировки по месяцам используй strftime('%Y-%m', \"Дата\") или, если формат даты dd.mm.YYYY, конструкцию substr(\"Дата\",7,4)||'-'||substr(\"Дата\",4,2) с алиасом month; затем GROUP BY month, ORDER BY month.\n"
            "- Если в числах используются запятые/пробелы, приведи к числу через CAST(REPLACE(REPLACE(\"Колонка\", ' ', ''), ',', '.') AS FLOAT).\n"
            "- Не используй обратные кавычки и одиночные кавычки для идентификаторов, только двойные кавычки.\n"
            "- Не добавляй лишних подзапросов и нестандартных функций."
        )
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
            "для топ-N используй ORDER BY и LIMIT; для частот: COUNT(*) AS cnt и GROUP BY. "
            "ВСЕГДА заключай имена таблиц и колонок в двойные кавычки, особенно если имя содержит пробелы/кириллицу/символы или начинается с цифры. "
            "Примеры: FROM \"1810_2\", SELECT \"Откр.\", \"Изм. %\". Не придумывай новые колонки — используй имена ровно как в схеме/превью."
        )
    else:
        rules = (
            "Правила (SQLite): используй двойные кавычки для имён, избегай небезопасных операций, не делай DELETE/UPDATE/INSERT; "
            "для топ-N используй ORDER BY и LIMIT; для частот: COUNT(*) AS cnt и GROUP BY. "
            "ВСЕГДА заключай имена таблиц и колонок в двойные кавычки, особенно если имя содержит пробелы/кириллицу/символы или начинается с цифры. "
            "Примеры: FROM \"1810_2\", SELECT \"Откр.\", \"Изм. %\". Не придумывай новые колонки и не переводй имена — используй их ровно как в схеме/превью (например, 'Дата' ≠ 'Date'). "
            "НЕ ИСПОЛЬЗУЙ DATE_TRUNC/DATE_FORMAT/EXTRACT/TO_CHAR — в SQLite их нет. Для группировки по месяцам: "
            "если дата в формате YYYY-MM-DD — используй substr(\"Дата\",1,7) как month; "
            "если неизвестный текстовый формат — считай ключ месяца как COALESCE(strftime('%Y-%m', \"Дата\"), substr(\"Дата\",1,7)). "
            "Дай алиас поля месяца 'month' и используй GROUP BY month, ORDER BY month."
        )
    policy = (
        "\nПолитика безопасности: Разрешены SELECT/WITH; допускаются ALTER TABLE ... RENAME COLUMN и UPDATE С ОБЯЗАТЕЛЬНЫМ WHERE. "
        "Если нужно выполнить несколько запросов, разделяй их ';;'. Возвращай только SQL."
    )
    checklist = (
        "\nПроверь перед выдачей SQL (чек‑лист):\n"
        "- Все имена таблиц/колонок взяты ИСКЛЮЧИТЕЛЬНО из схемы/превью выше, без переводов и с точным написанием.\n"
        "- ВСЕ идентификаторы в двойных кавычках: \"table\", \"column\" (включая таблицы вроде \"1810_2\").\n"
        "- Нет функций других СУБД (DATE_FORMAT, DATE_TRUNC, EXTRACT, TO_CHAR и т.п.).\n"
        "- Если нужен месяц, используй month как substr(\"Дата\",1,7) или strftime('%Y-%m', \"Дата\") и GROUP BY month.\n"
        "- Если в числах запятая/пробелы — используй CAST(REPLACE(REPLACE(\"Колонка\", ' ', ''), ',', '.') AS FLOAT).\n"
        "- Запрос начинается с SELECT или WITH и не содержит ; внутри.\n"
    )
    return prefix + "\n" + rules + policy + checklist


def generate_sql_from_nl(task: str, *, table_hint: Optional[str] = None) -> str:
    # Включаем примеры первых строк таблиц, чтобы LLM видела реальные имена колонок и значения
    schema_text = build_schema_summary(row_samples=3)
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
    # Не ограничиваем контекст: передаем все строки (осторожно с размером промпта)
    preview_rows = rows
    columns = list(preview_rows[0].keys()) if preview_rows else []
    system_text = (
        "Ты аналитик данных. Объясни результаты так, чтобы понял любой неспециалист. "
        "Говори простым языком, избегай жаргона и лишней математики.\n\n"
        "Структура ответа (всегда соблюдай её):\n"
        "1) Короткий итог — одна фраза с главным выводом (что больше/меньше, где максимум и т.п.).\n"
        "2) Что это значит — 1–2 предложения человеческим языком (польза/смысл для пользователя/бизнеса).\n"
        "3) Ключевые цифры — списком. Если есть категории: топ‑3 по метрике, минимум/максимум, среднее. "
        "Если есть 2 числовые колонки — сравни их (кто чаще больше, средняя разница). "
        "Если есть временная колонка (month/Дата) — укажи минимум/максимум с периодами и общий тренд.\n"
        "4) Рекомендации — 1 короткий пункт (что полезно сделать дальше).\n"
        "5) Ограничения — если данных мало/неполны, явно укажи.\n\n"
        "Правила:\n"
        "- Используй ТОЛЬКО переданные строки результата SQL. Если строк ≥ 1 — НЕ пиши, что данных нет.\n"
        "- Числа форматируй до 2 знаков. Не выдумывай новые метрики.\n"
        "- Если есть t_stat/p_value/f_stat — дай простой вердикт по значимости (p<0.05 → значимо).\n"
        "- Используй лаконичный Markdown, без кода и тройных кавычек.\n"
        "- Если дан блок DATA_FACTS — числа в ответе должны ему соответствовать."
    )
    # Сформируем факты из данных для повышения точности LLM
    facts_lines: List[str] = []
    try:
        if preview_rows:
            # Определим типы колонок и выберем вероятные измерение/метрику
            first = preview_rows[0]
            keys = list(first.keys())
            numeric_cols: List[str] = []
            text_cols: List[str] = []
            for k in keys:
                v = first.get(k)
                if isinstance(v, (int, float)):
                    numeric_cols.append(k)
                elif isinstance(v, str):
                    text_cols.append(k)
            def score_num(name: str) -> int:
                n = name.lower()
                score = 0
                if "avg" in n or "mean" in n:
                    score += 3
                if "rating" in n or "score" in n or "value" in n:
                    score += 2
                if n in ("y",):
                    score += 1
                return score
            metric = None
            if numeric_cols:
                metric = sorted(numeric_cols, key=lambda c: (-score_num(c), c))[0]
            # Выберем возможную категорию/время
            def score_text(name: str) -> int:
                n = name.lower()
                score = 0
                if "category" in n or "катег" in n:
                    score += 3
                if "name" in n or "type" in n or "group" in n:
                    score += 2
                if n in ("x", "month", "дата", "date"):
                    score += 1
                return score
            dim = None
            if text_cols:
                dim = sorted(text_cols, key=lambda c: (-score_text(c), c))[0]
            # Рассчитаем агрегаты по выбранной метрике
            if metric:
                vals: List[float] = []
                for r in preview_rows:
                    try:
                        v = r.get(metric)
                        if isinstance(v, (int, float)):
                            vals.append(float(v))
                    except Exception:
                        pass
                if vals:
                    mean_val = sum(vals) / max(1, len(vals))
                    facts_lines.append(f"rows_count={len(preview_rows)}")
                    facts_lines.append(f"metric={metric}")
                    facts_lines.append(f"mean_{metric}={mean_val:.4f}")
                    # Топ-5 по метрике, если есть измерение
                    if dim:
                        sorted_rows = sorted(
                            [r for r in preview_rows if isinstance(r.get(metric), (int, float))],
                            key=lambda r: float(r.get(metric)), reverse=True
                        )
                        top = sorted_rows[: min(5, len(sorted_rows))]
                        for i, r in enumerate(top, 1):
                            facts_lines.append(f"top{i}={r.get(dim)}:{float(r.get(metric)):.4f}")
                        # Минимум/максимум
                        if sorted_rows:
                            min_r = sorted_rows[-1]
                            max_r = sorted_rows[0]
                            facts_lines.append(f"min={min_r.get(dim)}:{float(min_r.get(metric)):.4f}")
                            facts_lines.append(f"max={max_r.get(dim)}:{float(max_r.get(metric)):.4f}")
    except Exception:
        pass
    facts_block = ("\nDATA_FACTS:\n" + "\n".join(facts_lines)) if facts_lines else ""
    user_text = (
        "Запрос пользователя: " + task.strip() + "\n"
        + "Выполненный SQL: " + sql.strip() + "\n"
        + "Колонки: " + ", ".join(columns) + "\n"
        + "Строки (превью): " + str(preview_rows)
        + facts_block
    )
    payload: Dict[str, Any] = {
        "modelUri": f"gpt://{settings.YANDEX_FOLDER_ID}/{settings.YANDEX_LLM_MODEL}/latest",
        "completionOptions": {"stream": False, "temperature": 0.0, "maxTokens": 2000},
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


