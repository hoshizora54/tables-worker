"""
Сервис для управления инструментами (Tools) для агентской системы.
Определяет доступные инструменты и их вызов.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Callable
from enum import Enum
from pydantic import BaseModel, Field

from backend.core import db
from backend.core.config import settings
from backend.core.db import run_select, run_execute
from backend.services.plot_service import render_plot_to_base64
import pandas as pd
from scipy import stats as scipy_stats
import re


class ToolType(str, Enum):
    """Типы инструментов"""
    SQL_QUERY = "sql_query"
    T_TEST = "t_test"
    ANOVA = "anova"
    MOVING_AVERAGE = "moving_average"
    GROUPBY = "groupby"
    PIVOT = "pivot"
    JOIN = "join"
    PLOT = "plot"
    FILTER = "filter"


class ToolParameter(BaseModel):
    """Параметр инструмента"""
    name: str
    type: str  # "string", "number", "boolean", "array"
    description: str
    required: bool = True


class ToolDefinition(BaseModel):
    """Определение инструмента"""
    name: str
    description: str
    parameters: List[ToolParameter]
    returns: str  # описание возвращаемого значения


class ToolCall(BaseModel):
    """Вызов инструмента от LLM"""
    tool_name: str
    parameters: Dict[str, Any]


# Регистр всех доступных инструментов
TOOLS_REGISTRY: Dict[str, Callable] = {}


def get_tools_definitions() -> List[ToolDefinition]:
    """Возвращает список всех доступных инструментов с их описаниями"""
    schema_text = _build_schema_summary()
    
    tools = [
        ToolDefinition(
            name="sql_query",
            description=(
                "Выполняет SQL SELECT запрос к базе данных. "
                "Используй для получения данных, фильтрации, агрегации и других операций с данными. "
                f"Доступные таблицы: {', '.join(db.list_tables()) if db.list_tables() else 'нет таблиц'}"
            ),
            parameters=[
                ToolParameter(
                    name="sql",
                    type="string",
                    description="SQL SELECT запрос (только SELECT или WITH, без опасных операций)",
                    required=True
                )
            ],
            returns="Список строк результата запроса"
        ),
        # Остальные инструменты будут скрыты, если включён режим LLM_ONLY_SQL
        ToolDefinition(
            name="t_test",
            description=(
                "Выполняет t-тест для сравнения средних значений двух групп. "
                "Используй когда нужно сравнить средние значения между двумя группами (например, 'сравни продажи в группах А и Б'). "
                "Также поддерживается режим для нескольких групп: передай параметр 'groups' (2 и более значений), будет выполнен попарный t‑тест (Welch) для всех пар."
            ),
            parameters=[
                ToolParameter(name="table", type="string", description="Название таблицы", required=True),
                ToolParameter(name="group_col", type="string", description="Название колонки с группами", required=True),
                ToolParameter(name="value_col", type="string", description="Название колонки с числовыми значениями для сравнения", required=True),
                ToolParameter(name="group_a", type="string", description="Значение первой группы", required=True),
                ToolParameter(name="group_b", type="string", description="Значение второй группы", required=True),
                ToolParameter(name="groups", type="array", description="Список значений групп (2+), для попарных t‑тестов", required=False),
                ToolParameter(name="where", type="string", description="Дополнительное SQL условие WHERE (опционально)", required=False),
                ToolParameter(name="equal_var", type="boolean", description="Предположение о равенстве дисперсий (по умолчанию False)", required=False)
            ],
            returns="Результат t-теста: t-статистика, p-value, средние значения и размеры групп"
        ),
        ToolDefinition(
            name="anova",
            description=(
                "Выполняет однофакторный ANOVA для сравнения средних значений нескольких групп. "
                "Используй когда нужно сравнить средние значения между тремя и более группами."
            ),
            parameters=[
                ToolParameter(name="table", type="string", description="Название таблицы", required=True),
                ToolParameter(name="group_col", type="string", description="Название колонки с группами", required=True),
                ToolParameter(name="value_col", type="string", description="Название колонки с числовыми значениями", required=True),
                ToolParameter(name="where", type="string", description="Дополнительное SQL условие WHERE (опционально)", required=False)
            ],
            returns="Результат ANOVA: F-статистика, p-value и статистика по группам"
        ),
        ToolDefinition(
            name="moving_average",
            description=(
                "Вычисляет скользящее среднее для временных рядов. "
                "Используй для сглаживания данных и анализа трендов."
            ),
            parameters=[
                ToolParameter(name="table", type="string", description="Название таблицы", required=True),
                ToolParameter(name="value_col", type="string", description="Колонка с числовыми значениями", required=True),
                ToolParameter(name="order_by", type="string", description="Колонка для сортировки (обычно дата или время)", required=True),
                ToolParameter(name="window", type="number", description="Размер окна для скользящего среднего (по умолчанию 7)", required=False),
                ToolParameter(name="partition_by", type="array", description="Колонки для разбиения на группы (опционально)", required=False),
                ToolParameter(name="limit", type="number", description="Максимальное количество строк (по умолчанию 1000)", required=False)
            ],
            returns="Таблица с исходными данными и колонкой moving_avg"
        ),
        ToolDefinition(
            name="groupby",
            description=(
                "Группирует данные и вычисляет агрегаты (sum, avg, min, max, count). "
                "Используй для подсчета статистики по группам."
            ),
            parameters=[
                ToolParameter(name="table", type="string", description="Название таблицы", required=True),
                ToolParameter(name="group_by", type="array", description="Список колонок для группировки", required=True),
                ToolParameter(name="aggregations", type="object", description="Словарь {колонка: функция}, где функция: sum|avg|min|max|count", required=True),
                ToolParameter(name="having", type="string", description="SQL условие HAVING (опционально)", required=False),
                ToolParameter(name="order_by", type="string", description="SQL выражение ORDER BY (опционально)", required=False),
                ToolParameter(name="limit", type="number", description="Максимальное количество строк (по умолчанию 1000)", required=False)
            ],
            returns="Таблица с результатами группировки и агрегации"
        ),
        ToolDefinition(
            name="pivot",
            description=(
                "Создает сводную таблицу (pivot table). "
                "Используй для преобразования данных из длинного формата в широкий."
            ),
            parameters=[
                ToolParameter(name="table", type="string", description="Название таблицы", required=True),
                ToolParameter(name="index", type="array", description="Колонки для строк (index)", required=True),
                ToolParameter(name="columns", type="string", description="Колонка для столбцов", required=True),
                ToolParameter(name="values", type="string", description="Колонка со значениями", required=True),
                ToolParameter(name="aggfunc", type="string", description="Функция агрегации: sum|mean|min|max|count (по умолчанию sum)", required=False),
                ToolParameter(name="fill_value", type="number", description="Значение для заполнения пропусков (по умолчанию 0)", required=False),
                ToolParameter(name="limit", type="number", description="Максимальное количество строк исходных данных (по умолчанию 100000)", required=False)
            ],
            returns="Сводная таблица"
        ),
        ToolDefinition(
            name="join",
            description=(
                "Объединяет две таблицы по ключам. "
                "Используй когда нужно объединить данные из разных таблиц."
            ),
            parameters=[
                ToolParameter(name="left_table", type="string", description="Левая таблица", required=True),
                ToolParameter(name="right_table", type="string", description="Правая таблица", required=True),
                ToolParameter(name="left_on", type="string", description="Ключ в левой таблице", required=True),
                ToolParameter(name="right_on", type="string", description="Ключ в правой таблице", required=True),
                ToolParameter(name="how", type="string", description="Тип соединения: inner|left (по умолчанию inner)", required=False),
                ToolParameter(name="new_table", type="string", description="Название новой таблицы (опционально, будет сгенерировано автоматически)", required=False)
            ],
            returns="Информация о созданной таблице и превью данных"
        ),
        ToolDefinition(
            name="plot",
            description=(
                "Создает график на основе данных. "
                "Используй когда пользователь просит построить график, визуализацию, диаграмму. "
                "Агент должен сам определить тип графика (bar, line, hist, scatter) и необходимые параметры."
            ),
            parameters=[
                ToolParameter(name="type", type="string", description="Тип графика: bar|line|hist|scatter", required=True),
                ToolParameter(name="table", type="string", description="Название таблицы", required=True),
                ToolParameter(name="x", type="string", description="Колонка для оси X (для hist - колонка с данными)", required=True),
                ToolParameter(name="y", type="string", description="Колонка для оси Y (для bar/line/scatter, опционально для bar с agg=count)", required=False),
                ToolParameter(name="hue", type="string", description="Колонка для группировки (опционально)", required=False),
                ToolParameter(name="agg", type="string", description="Функция агрегации для y: count|sum|mean|min|max (по умолчанию count)", required=False),
                ToolParameter(name="bins", type="number", description="Количество бинов для гистограммы (по умолчанию 20)", required=False),
                ToolParameter(name="where", type="string", description="SQL условие WHERE для фильтрации (опционально)", required=False),
                ToolParameter(name="title", type="string", description="Заголовок графика (опционально)", required=False),
                ToolParameter(name="limit", type="number", description="Максимальное количество точек данных (по умолчанию 100)", required=False)
            ],
            returns="Base64-кодированное изображение графика и данные"
        ),
        ToolDefinition(
            name="filter",
            description=(
                "Фильтрует строки таблицы по условию. "
                "Используй для выборки данных по условию."
            ),
            parameters=[
                ToolParameter(name="table", type="string", description="Название таблицы", required=True),
                ToolParameter(name="where", type="string", description="SQL условие WHERE", required=False),
                ToolParameter(name="order_by", type="string", description="SQL выражение ORDER BY (опционально)", required=False),
                ToolParameter(name="limit", type="number", description="Максимальное количество строк (по умолчанию 1000)", required=False)
            ],
            returns="Отфильтрованные строки"
        )
    ]
    if settings.LLM_ONLY_SQL:
        # Возвращаем только sql_query
        return [t for t in tools if t.name == "sql_query"]
    return tools


def _build_schema_summary() -> str:
    """Строит краткое описание схемы БД"""
    tables = db.list_tables()
    lines = []
    for t in tables:
        cols = db.get_table_columns(t)
        col_defs = ", ".join([f"{c['name']} ({c['type']})" for c in cols])
        lines.append(f"- {t}: {col_defs}")
    return "\n".join(lines) if lines else "Нет таблиц в базе данных"


_IDENTIFIER_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def _escape_identifier(name: str) -> str:
    """
    Безопасно оборачивает имя столбца/таблицы в двойные кавычки.
    Экранирует двойные кавычки внутри имени.
    """
    return '"' + str(name).replace('"', '""') + '"'


def _is_simple_identifier(token: str) -> bool:
    """
    Простой идентификатор SQL (без пробелов, пунктуации, не начинается с цифры).
    """
    return bool(_IDENTIFIER_RE.match(token))


def _auto_quote_simple_select(sql: str) -> str:
    """
    Пытается автоматически проставить кавычки для простых запросов вида:
      SELECT col1, col 2, Изм. %, Откр. FROM 1810_2
    Преобразует в:
      SELECT "col1", "col 2", "Изм. %", "Откр." FROM "1810_2"

    Ограничения: работает только для простых SELECT без подзапросов и функций в списке полей.
    Если распарсить не удаётся, возвращает исходный SQL.
    """
    sql_stripped = sql.strip()
    upper = sql_stripped.upper()
    if not (upper.startswith("SELECT") and " FROM " in upper):
        return sql

    # Разделяем на SELECT <fields> FROM <rest>
    try:
        # Найдём позицию первого FROM, не в скобках
        up = upper
        from_pos = up.find(" FROM ")
        if from_pos == -1:
            return sql
        select_clause = sql_stripped[len("SELECT"):from_pos].strip()
        rest = sql_stripped[from_pos + len(" FROM "):].strip()

        # rest может содержать WHERE/GROUP/ORDER/LIMIT; отделим имя таблицы
        m = re.match(r'([^\s]+)(.*)$', rest, flags=re.IGNORECASE)
        if not m:
            return sql
        table_token = m.group(1)
        tail = m.group(2) or ""

        # Разобьём поля по запятым, учитывая, что это простой случай (без функций)
        raw_fields = [f.strip() for f in select_clause.split(",")]
        quoted_fields = []
        for f in raw_fields:
            # Если уже есть кавычки или звёздочка/функция — не трогаем
            if f.startswith('"') and f.endswith('"'):
                quoted_fields.append(f)
            elif f == "*" or "(" in f or ")" in f:
                quoted_fields.append(f)
            else:
                quoted_fields.append(_escape_identifier(f))

        # Кавычим таблицу, если она не в кавычках
        if table_token.startswith('"') and table_token.endswith('"'):
            quoted_table = table_token
        else:
            quoted_table = _escape_identifier(table_token)

        rebuilt = f"SELECT {', '.join(quoted_fields)} FROM {quoted_table}{tail}"
        return rebuilt
    except Exception:
        return sql


def _extract_table_name(sql: str) -> Optional[str]:
    """
    Пытается извлечь имя таблицы из выражения FROM ... (первое вхождение).
    Возвращает без кавычек.
    """
    try:
        m = re.search(r'FROM\s+("([^"]+)"|([^\s;]+))', sql, flags=re.IGNORECASE)
        if not m:
            return None
        if m.group(2):
            return m.group(2)
        if m.group(3):
            # обрежем хвостовые символы
            token = m.group(3).strip()
            # снимем алиасы, если есть
            token = token.split()[0]
            return token.strip('"')
        return None
    except Exception:
        return None


def _apply_column_aliases(sql: str, table: str) -> str:
    """
    Подменяет популярные англ. алиасы колонок на реальные русские имена, если такие есть в таблице.
    Например: Open -> "Откр.", High -> "Макс.", Low -> "Мин.", Volume -> "Объём", Change/Change% -> "Изм. %", Date -> "Дата".
    """
    try:
        cols = [c["name"] for c in db.get_table_columns(table)]
        cols_lower = {c.lower(): c for c in cols}
        def exists(name: str) -> bool:
            return name in cols or name in cols_lower.values()
        # Кандидаты подмен
        candidates: Dict[str, str] = {}
        mapping_pref: Dict[str, str] = {
            "open": "Откр.",
            "high": "Макс.",
            "low": "Мин.",
            "close": "Закр.",
            "volume": "Объём",
            "change": "Изм. %",
            "change%": "Изм. %",
            "change_percent": "Изм. %",
            "date": "Дата",
        }
        for eng, ru in mapping_pref.items():
            # берем только те, что реально есть в таблице
            if any(ru == c or ru.lower() == c.lower() for c in cols):
                candidates[eng] = next((c for c in cols if c.lower() == ru.lower()), ru)
        # Применяем замены только для некавыченных токенов
        out = sql
        for eng, actual in candidates.items():
            pattern = re.compile(rf'(?<!")\b{re.escape(eng)}\b(?!")', flags=re.IGNORECASE)
            out = pattern.sub(_escape_identifier(actual), out)
        return out
    except Exception:
        return sql


def _normalize_casts_to_float(sql: str) -> str:
    """
    Преобразует CAST(<col> AS FLOAT) -> CAST(REPLACE(REPLACE(<col>, ' ', ''), ',', '.') AS FLOAT)
    чтобы корректно парсить числа с пробелами и запятыми.
    """
    try:
        pattern = re.compile(
            r'CAST\(\s*(?P<col>"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_\.]*)\s+AS\s+FLOAT\s*\)',
            flags=re.IGNORECASE,
        )
        def repl(m: re.Match) -> str:
            col = m.group("col")
            return f"CAST(REPLACE(REPLACE({col}, ' ', ''), ',', '.') AS FLOAT)"
        return pattern.sub(repl, sql)
    except Exception:
        return sql


def _widen_year_like(sql: str) -> str:
    """
    Расширяет фильтры вида: <col> LIKE 'YYYY%' -> (<col> LIKE 'YYYY%' OR <col> LIKE '%YYYY' OR <col> LIKE '%YYYY%')
    чтобы поддержать форматы dd.mm.YYYY и YYYY-mm-dd.
    """
    try:
        pattern = re.compile(
            r'(?P<col>"[^"]+"|[A-Za-z_][A-Za-z0-9_\.]*)\s+LIKE\s+[\'"](?P<year>\d{4})%[\'"]',
            flags=re.IGNORECASE,
        )
        def repl(m: re.Match) -> str:
            col = m.group("col")
            year = m.group("year")
            return f"({col} LIKE '{year}%' OR {col} LIKE '%{year}' OR {col} LIKE '%{year}%')"
        return pattern.sub(repl, sql)
    except Exception:
        return sql


def _rewrite_date_trunc(sql: str) -> str:
    """
    Переписывает DATE_TRUNC('month', col) в безопасное выражение для SQLite:
      CASE WHEN dd.mm.YYYY -> YYYY-MM
           WHEN ISO YYYY-MM-DD -> YYYY-MM
           ELSE strftime('%Y-%m', col)
      END
    Работает независимо от регистра.
    """
    try:
        pattern = re.compile(
            r"DATE_TRUNC\s*\(\s*['\"]month['\"]\s*,\s*(?P<col>[^)]+?)\s*\)",
            flags=re.IGNORECASE,
        )
        def repl(m: re.Match) -> str:
            col = m.group("col").strip()
            # dd.mm.YYYY -> YYYY-MM; ISO (YYYY-MM-DD) -> YYYY-MM; иначе попытка через strftime
            return (
                "CASE "
                f"WHEN {col} LIKE '__.__.____' THEN substr({col},7,4)||'-'||substr({col},4,2) "
                f"WHEN substr({col},5,1)='-' AND substr({col},8,1)='-' THEN substr({col},1,7) "
                f"ELSE COALESCE(strftime('%Y-%m', {col}), substr({col},1,7)) "
                "END"
            )
        return pattern.sub(repl, sql)
    except Exception:
        return sql


def _normalize_identifier(name: str) -> str:
    """
    Нормализует имя идентификатора: если уже в кавычках — снимает их и экранирует заново.
    Иначе — просто экранирует.
    """
    s = str(name).strip()
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        inner = s[1:-1].replace('""', '"')
        return _escape_identifier(inner)
    return _escape_identifier(s)


def _strip_wrapped_quotes(val: Any) -> Any:
    """
    Для значений (а не идентификаторов): если строка обёрнута кавычками '...' или "..." — снимаем внешние кавычки.
    """
    if isinstance(val, str):
        s = val.strip()
        if len(s) >= 2:
            if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
                return s[1:-1]
    return val


def call_tool(tool_call: ToolCall) -> Dict[str, Any]:
    """
    Вызывает инструмент по его имени с переданными параметрами.
    Возвращает результат выполнения инструмента.
    """
    tool_name = tool_call.tool_name
    params = tool_call.parameters
    
    # В режиме «чисто LLM» разрешаем только sql_query
    if settings.LLM_ONLY_SQL and tool_name != "sql_query":
        raise ValueError("Режим LLM_ONLY_SQL: доступен только инструмент 'sql_query'. Сгенерируй корректный SELECT/WITH для SQLite.")

    if tool_name == "sql_query":
        sql = params.get("sql", "")
        if not sql.strip().upper().startswith(("SELECT", "WITH")):
            raise ValueError("Разрешены только SELECT/WITH запросы")
        # Режим «чисто LLM»: выполняем SQL как есть, без авто-правок и переписываний
        rows = run_select(sql)
        return {"rows": rows, "count": len(rows)}
    
    elif tool_name == "t_test":
        # Валидируем наличие обязательных параметров
        table = params.get("table")
        group_col = params.get("group_col")
        value_col = params.get("value_col")
        group_a = params.get("group_a")
        group_b = params.get("group_b")
        missing = [k for k, v in {"table": table, "group_col": group_col, "value_col": value_col}.items() if not v]
        if missing:
            raise ValueError(
                "t_test: отсутствуют обязательные параметры: "
                + ", ".join(missing)
                + ". Укажи table, group_col, value_col, а также group_a и group_b (или groups[] для попарных сравнений)."
            )
        groups_list = params.get("groups")
        where = params.get("where")
        equal_var = params.get("equal_var", False)
        
        q_table = _normalize_identifier(table)
        q_group_col = _normalize_identifier(group_col)
        q_value_col = _normalize_identifier(value_col)
        where_clause = f" WHERE {where} " if where else ""
        # Режим попарных сравнений: groups (2+ значений)
        if isinstance(groups_list, list) and len(groups_list) >= 2:
            clean_groups = [_strip_wrapped_quotes(g) for g in groups_list]
            # Сформируем плейсхолдеры для IN
            placeholders = ", ".join([f":g{i}" for i in range(len(clean_groups))])
            sql_all = (
                f"SELECT {q_group_col} AS grp, {q_value_col} AS val FROM {q_table}{where_clause} "
                f"AND {q_group_col} IN ({placeholders})"
                if where
                else f"SELECT {q_group_col} AS grp, {q_value_col} AS val FROM {q_table} "
                     f"WHERE {q_group_col} IN ({placeholders})"
            )
            params_in = {f"g{i}": clean_groups[i] for i in range(len(clean_groups))}
            rows_all = run_select(sql_all, params_in)
            data_by_group: Dict[str, List[float]] = {}
            for r in rows_all:
                g = r["grp"]
                if g is not None:
                    data_by_group.setdefault(str(g), []).append(r["val"])
            # Попарные сравнения (Welch по умолчанию)
            from itertools import combinations
            comps = []
            for a, b in combinations(clean_groups, 2):
                va = data_by_group.get(str(a), [])
                vb = data_by_group.get(str(b), [])
                if len(va) >= 2 and len(vb) >= 2:
                    tstat, pval = scipy_stats.ttest_ind(va, vb, equal_var=equal_var, nan_policy="omit")
                    comps.append({
                        "group_a": a, "group_b": b,
                        "t_stat": float(tstat), "p_value": float(pval),
                        "n_a": len(va), "n_b": len(vb),
                        "mean_a": float(sum(va)/len(va)) if va else None,
                        "mean_b": float(sum(vb)/len(vb)) if vb else None,
                        "diff": float((sum(vb)/len(vb) - sum(va)/len(va)) if (va and vb) else 0.0),
                    })
            # Простая поправка Бонферрони
            m = max(1, len(comps))
            for c in comps:
                padj = min(1.0, float(c["p_value"]) * m)
                c["p_value_adj_bonferroni"] = padj
            return {
                "comparisons": comps,
                "rows": comps,  # чтобы фронтенд показал таблицей
                "group_col": group_col,
                "value_col": value_col,
                "equal_var": bool(equal_var),
                "mode": "pairwise"
            }
        # Обычный режим: 2 группы (или авто-выбор)
        sql = (
            f"SELECT {q_group_col} AS grp, {q_value_col} AS val FROM {q_table}{where_clause} "
            f"AND {q_group_col} IN (:a, :b)"
            if where
            else f"SELECT {q_group_col} AS grp, {q_value_col} AS val FROM {q_table} "
                 f"WHERE {q_group_col} IN (:a, :b)"
        )
        rows = run_select(sql, {"a": _strip_wrapped_quotes(group_a), "b": _strip_wrapped_quotes(group_b)})
        vals_a = [r["val"] for r in rows if r["grp"] == _strip_wrapped_quotes(group_a)]
        vals_b = [r["val"] for r in rows if r["grp"] == _strip_wrapped_quotes(group_b)]

        # Если групп недостаточно, попробуем авто-выбор двух самых частых категорий
        need_autogroups = (len(vals_a) < 2 or len(vals_b) < 2)
        ua = str(_strip_wrapped_quotes(group_a)) if group_a is not None else ""
        ub = str(_strip_wrapped_quotes(group_b)) if group_b is not None else ""
        if ua == group_col or ua == value_col or ub == group_col or ub == value_col or not ua or not ub:
            need_autogroups = True

        auto_groups_used = False
        chosen_groups: list[str] = []
        if need_autogroups:
            top_sql = (
                f"SELECT {q_group_col} AS grp, COUNT(*) AS c FROM {q_table} "
                f"WHERE {q_group_col} IS NOT NULL GROUP BY {q_group_col} ORDER BY c DESC LIMIT 2"
            )
            top_rows = run_select(top_sql)
            if len(top_rows) >= 2:
                g1 = top_rows[0]["grp"]
                g2 = top_rows[1]["grp"]
                if g1 is not None and g2 is not None:
                    chosen_groups = [g1, g2]
                    sql2 = (
                        f"SELECT {q_group_col} AS grp, {q_value_col} AS val FROM {q_table}{where_clause} "
                        f"AND {q_group_col} IN (:a, :b)"
                        if where
                        else f"SELECT {q_group_col} AS grp, {q_value_col} AS val FROM {q_table} "
                             f"WHERE {q_group_col} IN (:a, :b)"
                    )
                    rows2 = run_select(sql2, {"a": g1, "b": g2})
                    vals_a = [r["val"] for r in rows2 if r["grp"] == g1]
                    vals_b = [r["val"] for r in rows2 if r["grp"] == g2]
                    auto_groups_used = True

        if len(vals_a) < 2:
            raise ValueError(f"Недостаточно данных в группе '{group_a}': найдено {len(vals_a)} значений (требуется минимум 2)")
        if len(vals_b) < 2:
            raise ValueError(f"Недостаточно данных в группе '{group_b}': найдено {len(vals_b)} значений (требуется минимум 2)")
        tstat, pval = scipy_stats.ttest_ind(vals_a, vals_b, equal_var=equal_var, nan_policy="omit")
        return {
            "t_stat": float(tstat),
            "p_value": float(pval),
            "n_a": len(vals_a),
            "n_b": len(vals_b),
            "mean_a": float(sum(vals_a) / len(vals_a)),
            "mean_b": float(sum(vals_b) / len(vals_b)),
            "auto_groups_used": auto_groups_used,
            "chosen_groups": chosen_groups,
            "group_col": group_col,
            "value_col": value_col,
            "mode": "binary"
        }
    
    elif tool_name == "anova":
        table = params["table"]
        group_col = params["group_col"]
        value_col = params["value_col"]
        where = params.get("where")
        
        q_table = _normalize_identifier(table)
        q_group_col = _normalize_identifier(group_col)
        q_value_col = _normalize_identifier(value_col)
        where_clause = f" WHERE {where} " if where else ""
        sql = f"SELECT {q_group_col} AS grp, {q_value_col} AS val FROM {q_table}{where_clause}"
        rows = run_select(sql)
        groups: Dict[str, List[float]] = {}
        for r in rows:
            g = str(r["grp"]) if r["grp"] is not None else "__NA__"
            groups.setdefault(g, []).append(r["val"])
        valid = [vals for vals in groups.values() if len(vals) >= 2]
        if len(valid) < 2:
            raise ValueError("Недостаточно групп для ANOVA")
        fstat, pval = scipy_stats.f_oneway(*valid)
        summary = {k: {"n": len(v), "mean": (sum(v) / len(v) if v else None)} for k, v in groups.items()}
        return {"f_stat": float(fstat), "p_value": float(pval), "groups": summary}
    
    elif tool_name == "moving_average":
        table = params["table"]
        value_col = params["value_col"]
        order_by = params["order_by"]
        window = params.get("window", 7)
        partition_by = params.get("partition_by")
        limit = params.get("limit", 1000)
        
        q_table = _normalize_identifier(table)
        q_value_col = _normalize_identifier(value_col)
        q_order_by = _normalize_identifier(order_by)
        part = ("PARTITION BY " + ", ".join([_normalize_identifier(p) for p in partition_by])) if partition_by else ""
        sql = (
            f"SELECT *, AVG({q_value_col}) OVER ("
            f" {part} ORDER BY {q_order_by} ROWS BETWEEN {max(window-1, 0)} PRECEDING AND CURRENT ROW"
            f") AS moving_avg FROM {q_table} LIMIT :lim"
        )
        rows = run_select(sql, {"lim": limit})
        return {"rows": rows, "count": len(rows)}
    
    elif tool_name == "groupby":
        table = params["table"]
        group_by = params["group_by"]
        aggregations = params["aggregations"]
        having = params.get("having")
        order_by = params.get("order_by")
        limit = params.get("limit", 1000)
        
        q_table = _normalize_identifier(table)
        gb = ", ".join([_normalize_identifier(g) for g in group_by])
        select_aggs: List[str] = []
        # Запомним алиасы по типу аггрегации (первый встретившийся), чтобы подменять в ORDER BY/HAVING
        first_alias_by_fn: Dict[str, str] = {}
        agg_allowed = ("sum", "avg", "min", "max", "count")
        for col, fn in aggregations.items():
            fn_up = str(fn).lower()
            if fn_up not in agg_allowed:
                raise ValueError(f"Неподдерживаемая агрегация: {fn}")
            col_str = str(col)
            is_expr = ("(" in col_str) or (")" in col_str) or (" " in col_str)
            # Определим, есть ли уже агрегатная функция в выражении
            m_agg = re.match(r'^\s*(sum|avg|min|max|count)\s*\(', col_str, flags=re.IGNORECASE)
            already_agg = bool(m_agg)
            actual_fn_for_alias = fn_up
            if already_agg:
                actual_fn_for_alias = m_agg.group(1).lower()
            # Построим выражение и алиас
            if is_expr:
                # Не ковычим выражение, используем как есть
                if already_agg:
                    expr_sql = col_str
                else:
                    expr_sql = f"{fn_up.upper()}({col_str})"
                # Генерируем компактный алиас из выражения
                alias_base = re.sub(r'[^A-Za-z0-9_]+', '_', col_str).strip('_').lower()
                if len(alias_base) > 40:
                    alias_base = alias_base[:40]
                alias = f"{actual_fn_for_alias}_{alias_base}" if alias_base else f"{actual_fn_for_alias}_expr"
                select_aggs.append(f"{expr_sql} AS \"{alias}\"")
            else:
                alias = f"{fn_up}_{col_str}"
                if fn_up == "count" and col_str == "*":
                    select_aggs.append(f"COUNT(*) AS \"{alias}\"")
                else:
                    select_aggs.append(f"{fn_up.upper()}({_normalize_identifier(col_str)}) AS \"{alias}\"")
            # Сохраним первый алиас для данного типа агрегата
            if actual_fn_for_alias not in first_alias_by_fn:
                first_alias_by_fn[actual_fn_for_alias] = alias
        sel = ", ".join([gb] + select_aggs)
        sql = f"SELECT {sel} FROM {q_table} GROUP BY {gb}"

        # Умеренно нормализуем HAVING: заменим голые имена функций на алиасы и отфильтруем опасные конструкции
        def _normalize_cond_expr(expr: str) -> str:
            try:
                s = expr or ""
                # Подмена: count -> "count_<col>", sum -> "sum_<col>", ...
                for fn_name, alias_name in first_alias_by_fn.items():
                    # Заменяем только голое слово (не COUNT(...))
                    pattern = re.compile(r'\b' + re.escape(fn_name) + r'\b(?!\s*\()', flags=re.IGNORECASE)
                    s = pattern.sub(f'"{alias_name}"', s)
                # Кавычим имена колонок из group_by, если они встречаются без кавычек
                for g in group_by:
                    g_norm = _normalize_identifier(g)
                    pattern_col = re.compile(r'(?<!")\b' + re.escape(str(g)) + r'\b(?!")', flags=re.IGNORECASE)
                    s = pattern_col.sub(g_norm, s)
                return s
            except Exception:
                # В случае любой ошибки с регулярками — возвращаем исходное выражение
                return expr or ""

        # Если HAVING содержит неподдерживаемые подзапросы/ALL/GROUP BY — игнорируем HAVING во избежание ошибок SQLite
        if having:
            having_suspicious = re.search(r'\bALL\s*\(|\bGROUP\s+BY\b|\bSELECT\b', having, flags=re.IGNORECASE)
            if not having_suspicious:
                having_norm = _normalize_cond_expr(having)
                sql += f" HAVING {having_norm}"

        # Нормализуем ORDER BY
        if order_by:
            order_norm = _normalize_cond_expr(order_by)
            sql += f" ORDER BY {order_norm}"
        sql += " LIMIT :lim"
        rows = run_select(sql, {"lim": limit})
        return {"rows": rows, "count": len(rows)}
    
    elif tool_name == "pivot":
        table = params["table"]
        index = params["index"]
        columns = params["columns"]
        values = params["values"]
        aggfunc = params.get("aggfunc", "sum")
        fill_value = params.get("fill_value", 0.0)
        limit = params.get("limit", 100000)
        
        from backend.core.db import get_engine
        engine = get_engine()
        q_table = _normalize_identifier(table)
        df = pd.read_sql(f"SELECT * FROM {q_table} LIMIT {int(limit)}", con=engine)
        agg = aggfunc.lower()
        if agg not in ("sum", "mean", "min", "max", "count"):
            raise ValueError(f"Неподдерживаемая агрегация: {agg}")
        func = {"sum": "sum", "mean": "mean", "min": "min", "max": "max", "count": "count"}[agg]
        pvt = pd.pivot_table(df, index=index, columns=columns, values=values, aggfunc=func, fill_value=fill_value)
        pvt = pvt.reset_index()
        pvt.columns = [str(c) for c in pvt.columns]
        rows = pvt.to_dict(orient="records")
        return {"rows": rows, "count": len(rows)}
    
    elif tool_name == "join":
        left_table = params["left_table"]
        right_table = params["right_table"]
        left_on = params["left_on"]
        right_on = params["right_on"]
        how = params.get("how", "inner")
        new_table = params.get("new_table")
        
        if how not in ("inner", "left"):
            raise ValueError("Поддерживаются только INNER и LEFT JOIN")
        new_table = new_table or f"{left_table}__join__{right_table}"
        join_kw = "LEFT JOIN" if how == "left" else "INNER JOIN"
        q_left_table = _normalize_identifier(left_table)
        q_right_table = _normalize_identifier(right_table)
        q_left_on = _normalize_identifier(left_on)
        q_right_on = _normalize_identifier(right_on)
        q_new_table = _normalize_identifier(new_table)
        sql_create = f"SELECT l.*, r.* FROM {q_left_table} l {join_kw} {q_right_table} r ON l.{q_left_on} = r.{q_right_on}"
        run_execute(f"DROP TABLE IF EXISTS {q_new_table}")
        run_execute(f"CREATE TABLE {q_new_table} AS {sql_create}")
        from backend.services.versioning_service import snapshot_table
        try:
            snapshot_table(new_table, operation="join", details=f"{left_table}.{left_on} {how} {right_table}.{right_on}")
        except Exception:
            pass
        rows = run_select(f"SELECT * FROM {q_new_table} LIMIT 50")
        return {"status": "ok", "table": new_table, "preview": rows}
    
    elif tool_name == "plot":
        chart_type = params["type"].lower()
        table = params["table"]
        x = params["x"]
        y = params.get("y")
        hue = params.get("hue")
        agg = params.get("agg", "count").lower()
        bins = params.get("bins", 20)
        where = params.get("where")
        title = params.get("title")
        limit = params.get("limit", 100)
        
        where_clause = f" WHERE {where} " if where else ""
        
        q_table = _normalize_identifier(table)
        q_x = _normalize_identifier(x)
        q_y = _normalize_identifier(y) if y else None
        if chart_type in ("bar", "line"):
            if not y and agg == "count":
                sql = f"SELECT {q_x} AS x, COUNT(*) AS y FROM {q_table} {where_clause} GROUP BY {q_x} ORDER BY y DESC LIMIT :lim"
            elif y:
                if agg == "mean":
                    sql = f"SELECT {q_x} AS x, AVG({q_y}) AS y FROM {q_table} {where_clause} GROUP BY {q_x} ORDER BY y DESC LIMIT :lim"
                elif agg == "sum":
                    sql = f"SELECT {q_x} AS x, SUM({q_y}) AS y FROM {q_table} {where_clause} GROUP BY {q_x} ORDER BY y DESC LIMIT :lim"
                elif agg == "min":
                    sql = f"SELECT {q_x} AS x, MIN({q_y}) AS y FROM {q_table} {where_clause} GROUP BY {q_x} ORDER BY y DESC LIMIT :lim"
                elif agg == "max":
                    sql = f"SELECT {q_x} AS x, MAX({q_y}) AS y FROM {q_table} {where_clause} GROUP BY {q_x} ORDER BY y DESC LIMIT :lim"
                else:
                    sql = f"SELECT {q_x} AS x, COUNT({q_y}) AS y FROM {q_table} {where_clause} GROUP BY {q_x} ORDER BY y DESC LIMIT :lim"
            else:
                sql = f"SELECT {q_x} AS x, COUNT(*) AS y FROM {q_table} {where_clause} GROUP BY {q_x} ORDER BY y DESC LIMIT :lim"
            rows = run_select(sql, params={"lim": limit})
            df = pd.DataFrame(rows)
            img = render_plot_to_base64(chart_type=chart_type, data=df, x="x", y="y", hue=hue, title=title)
            return {"image_base64": img, "rows": rows, "type": chart_type}
        
        elif chart_type == "hist":
            rows = run_select(f"SELECT {q_x} AS x FROM {q_table} {where_clause} LIMIT 5000")
            df = pd.DataFrame(rows)
            img = render_plot_to_base64(chart_type="hist", data=df, x="x", bins=bins, title=title)
            return {"image_base64": img, "rows": rows, "bins": bins, "type": "hist"}
        
        elif chart_type == "scatter":
            if not y:
                raise ValueError("Для scatter графика требуется параметр y")
            rows = run_select(f"SELECT {q_x} AS x, {q_y} AS y FROM {q_table} {where_clause} LIMIT :lim", params={"lim": limit})
            df = pd.DataFrame(rows)
            img = render_plot_to_base64(chart_type="scatter", data=df, x="x", y="y", hue=hue, title=title)
            return {"image_base64": img, "rows": rows, "type": "scatter"}
        
        else:
            raise ValueError(f"Неизвестный тип графика: {chart_type}")
    
    elif tool_name == "filter":
        table = params["table"]
        where = params.get("where")
        order_by = params.get("order_by")
        limit = params.get("limit", 1000)
        
        where_clause = f" WHERE {where} " if where else ""
        order_clause = f" ORDER BY {order_by} " if order_by else ""
        q_table = _normalize_identifier(table)
        sql = f"SELECT * FROM {q_table}{where_clause}{order_clause} LIMIT :lim"
        rows = run_select(sql, {"lim": limit})
        return {"rows": rows, "count": len(rows)}
    
    else:
        raise ValueError(f"Неизвестный инструмент: {tool_name}")

