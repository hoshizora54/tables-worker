"""
Сервис для управления инструментами (Tools) для агентской системы.
Определяет доступные инструменты и их вызов.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Callable
from enum import Enum
from pydantic import BaseModel, Field

from backend.core import db
from backend.core.db import run_select, run_execute
from backend.services.plot_service import render_plot_to_base64
import pandas as pd
from scipy import stats as scipy_stats
from math import ceil


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
    
    return [
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
        ToolDefinition(
            name="t_test",
            description=(
                "Выполняет t-тест для сравнения средних значений двух групп. "
                "Используй когда нужно сравнить средние значения между двумя группами (например, 'сравни продажи в группах А и Б')."
            ),
            parameters=[
                ToolParameter(name="table", type="string", description="Название таблицы", required=True),
                ToolParameter(name="group_col", type="string", description="Название колонки с группами", required=True),
                ToolParameter(name="value_col", type="string", description="Название колонки с числовыми значениями для сравнения", required=True),
                ToolParameter(name="group_a", type="string", description="Значение первой группы", required=True),
                ToolParameter(name="group_b", type="string", description="Значение второй группы", required=True),
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


def _build_schema_summary() -> str:
    """Строит краткое описание схемы БД"""
    tables = db.list_tables()
    lines = []
    for t in tables:
        cols = db.get_table_columns(t)
        col_defs = ", ".join([f"{c['name']} ({c['type']})" for c in cols])
        lines.append(f"- {t}: {col_defs}")
    return "\n".join(lines) if lines else "Нет таблиц в базе данных"


def call_tool(tool_call: ToolCall) -> Dict[str, Any]:
    """
    Вызывает инструмент по его имени с переданными параметрами.
    Возвращает результат выполнения инструмента.
    """
    tool_name = tool_call.tool_name
    params = tool_call.parameters
    
    if tool_name == "sql_query":
        sql = params.get("sql", "")
        if not sql.strip().upper().startswith(("SELECT", "WITH")):
            raise ValueError("Разрешены только SELECT/WITH запросы")
        rows = run_select(sql)
        return {"rows": rows, "count": len(rows)}
    
    elif tool_name == "t_test":
        table = params["table"]
        group_col = params["group_col"]
        value_col = params["value_col"]
        group_a = params["group_a"]
        group_b = params["group_b"]
        where = params.get("where")
        equal_var = params.get("equal_var", False)
        
        where_clause = f" WHERE {where} " if where else ""
        sql = (
            f'SELECT "{group_col}" AS grp, "{value_col}" AS val FROM "{table}"{where_clause} '
            f'AND "{group_col}" IN (:a, :b)'
            if where
            else f'SELECT "{group_col}" AS grp, "{value_col}" AS val FROM "{table}" '
                 f'WHERE "{group_col}" IN (:a, :b)'
        )
        rows = run_select(sql, {"a": group_a, "b": group_b})
        vals_a = [r["val"] for r in rows if r["grp"] == group_a]
        vals_b = [r["val"] for r in rows if r["grp"] == group_b]
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
        }
    
    elif tool_name == "anova":
        table = params["table"]
        group_col = params["group_col"]
        value_col = params["value_col"]
        where = params.get("where")
        
        where_clause = f" WHERE {where} " if where else ""
        sql = f'SELECT "{group_col}" AS grp, "{value_col}" AS val FROM "{table}"{where_clause}'
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
        
        part = (
            ("PARTITION BY " + ", ".join([f'"{p}"' for p in partition_by]))
            if partition_by
            else ""
        )
        sql = (
            f"SELECT *, AVG(\"{value_col}\") OVER ("
            f" {part} ORDER BY \"{order_by}\" ROWS BETWEEN {max(window-1, 0)} PRECEDING AND CURRENT ROW"
            f") AS moving_avg FROM \"{table}\" LIMIT :lim"
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
        
        gb = ", ".join([f'"{g}"' for g in group_by])
        select_aggs: List[str] = []
        for col, fn in aggregations.items():
            fn_up = fn.lower()
            if fn_up not in ("sum", "avg", "min", "max", "count"):
                raise ValueError(f"Неподдерживаемая агрегация: {fn}")
            alias = f"{fn_up}_{col}"
            if fn_up == "count" and col == "*":
                select_aggs.append(f"COUNT(*) AS \"{alias}\"")
            else:
                select_aggs.append(f"{fn_up.upper()}(\"{col}\") AS \"{alias}\"")
        sel = ", ".join([gb] + select_aggs)
        sql = f"SELECT {sel} FROM \"{table}\" GROUP BY {gb}"
        if having:
            sql += f" HAVING {having}"
        if order_by:
            sql += f" ORDER BY {order_by}"
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
        df = pd.read_sql(f'SELECT * FROM "{table}" LIMIT {int(limit)}', con=engine)
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
        sql_create = (
            f'SELECT l.*, r.* FROM "{left_table}" l {join_kw} "{right_table}" r ON l."{left_on}" = r."{right_on}"'
        )
        run_execute(f'DROP TABLE IF EXISTS "{new_table}"')
        run_execute(f'CREATE TABLE "{new_table}" AS {sql_create}')
        from backend.services.versioning_service import snapshot_table
        try:
            snapshot_table(new_table, operation="join", details=f"{left_table}.{left_on} {how} {right_table}.{right_on}")
        except Exception:
            pass
        rows = run_select(f'SELECT * FROM "{new_table}" LIMIT 50')
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
        
        if chart_type in ("bar", "line"):
            if not y and agg == "count":
                sql = (
                    f'SELECT "{x}" AS x, COUNT(*) AS y FROM "{table}" {where_clause} GROUP BY "{x}" ORDER BY y DESC LIMIT :lim'
                )
            elif y:
                if agg == "mean":
                    sql = f'SELECT "{x}" AS x, AVG("{y}") AS y FROM "{table}" {where_clause} GROUP BY "{x}" ORDER BY y DESC LIMIT :lim'
                elif agg == "sum":
                    sql = f'SELECT "{x}" AS x, SUM("{y}") AS y FROM "{table}" {where_clause} GROUP BY "{x}" ORDER BY y DESC LIMIT :lim'
                elif agg == "min":
                    sql = f'SELECT "{x}" AS x, MIN("{y}") AS y FROM "{table}" {where_clause} GROUP BY "{x}" ORDER BY y DESC LIMIT :lim'
                elif agg == "max":
                    sql = f'SELECT "{x}" AS x, MAX("{y}") AS y FROM "{table}" {where_clause} GROUP BY "{x}" ORDER BY y DESC LIMIT :lim'
                else:
                    sql = f'SELECT "{x}" AS x, COUNT("{y}") AS y FROM "{table}" {where_clause} GROUP BY "{x}" ORDER BY y DESC LIMIT :lim'
            else:
                sql = f'SELECT "{x}" AS x, COUNT(*) AS y FROM "{table}" {where_clause} GROUP BY "{x}" ORDER BY y DESC LIMIT :lim'
            rows = run_select(sql, params={"lim": limit})
            df = pd.DataFrame(rows)
            img = render_plot_to_base64(chart_type=chart_type, data=df, x="x", y="y", hue=hue, title=title)
            return {"image_base64": img, "rows": rows, "type": chart_type}
        
        elif chart_type == "hist":
            rows = run_select(f'SELECT "{x}" AS x FROM "{table}" {where_clause} LIMIT 5000')
            df = pd.DataFrame(rows)
            img = render_plot_to_base64(chart_type="hist", data=df, x="x", bins=bins, title=title)
            return {"image_base64": img, "rows": rows, "bins": bins, "type": "hist"}
        
        elif chart_type == "scatter":
            if not y:
                raise ValueError("Для scatter графика требуется параметр y")
            rows = run_select(f'SELECT "{x}" AS x, "{y}" AS y FROM "{table}" {where_clause} LIMIT :lim', params={"lim": limit})
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
        sql = f'SELECT * FROM "{table}"{where_clause}{order_clause} LIMIT :lim'
        rows = run_select(sql, {"lim": limit})
        return {"rows": rows, "count": len(rows)}
    
    else:
        raise ValueError(f"Неизвестный инструмент: {tool_name}")

