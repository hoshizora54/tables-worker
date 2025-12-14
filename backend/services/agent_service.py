"""
Агентский сервис с поддержкой Tool Use.
Реализует агентский цикл: LLM анализирует запрос, выбирает инструменты и вызывает их.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import json
import re
import math

import requests

from backend.core.config import settings
from backend.core import db
from backend.services.tool_service import (
    get_tools_definitions,
    call_tool,
    ToolCall,
    ToolDefinition
)
from backend.services.llm_service import generate_answer_from_rows
import logging


def _build_tools_prompt(tools: List[ToolDefinition]) -> str:
    """Строит промпт с описанием доступных инструментов"""
    lines = ["Доступные инструменты:"]
    for tool in tools:
        lines.append(f"\n{tool.name}: {tool.description}")
        lines.append("Параметры:")
        for param in tool.parameters:
            req_mark = "(обязательно)" if param.required else "(опционально)"
            lines.append(f"  - {param.name} ({param.type}) {req_mark}: {param.description}")
        lines.append(f"Возвращает: {tool.returns}")
    return "\n".join(lines)


def _build_schema_summary() -> str:
    """Строит краткое описание схемы БД"""
    tables = db.list_tables()
    if not tables:
        return "Нет таблиц в базе данных"
    lines = ["Схема базы данных:"]
    for t in tables:
        cols = db.get_table_columns(t)
        col_defs = ", ".join([f"{c['name']} ({c['type']})" for c in cols])
        lines.append(f"- {t}: {col_defs}")
    return "\n".join(lines)


def _parse_tool_call_from_llm(text: str) -> Optional[ToolCall]:
    """
    Парсит вызов инструмента из ответа LLM.
    Ожидаемый формат: JSON с полями tool_name и parameters.
    Может быть обернут в markdown code block или просто JSON.
    """
    # Убираем markdown code blocks
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    text = text.strip()
    
    # Пытаемся найти JSON объект
    json_match = re.search(r'\{[^{}]*"tool_name"[^{}]*\}', text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(0))
            if "tool_name" in data and "parameters" in data:
                return ToolCall(
                    tool_name=data["tool_name"],
                    parameters=data["parameters"]
                )
        except json.JSONDecodeError:
            pass
    
    # Пытаемся распарсить весь текст как JSON
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "tool_name" in data and "parameters" in data:
            return ToolCall(
                tool_name=data["tool_name"],
                parameters=data["parameters"]
            )
    except json.JSONDecodeError:
        pass
    
    return None


def _call_yandex_llm(messages: List[Dict[str, str]], max_tokens: int = 2000) -> str:
    """Вызывает Yandex GPT API"""
    headers = {
        "Authorization": f"Api-Key {settings.YANDEX_API_KEY}",
        "x-folder-id": settings.YANDEX_FOLDER_ID,
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "modelUri": f"gpt://{settings.YANDEX_FOLDER_ID}/{settings.YANDEX_LLM_MODEL}/latest",
        "completionOptions": {
            "stream": False,
            "temperature": 0.1,  # Немного выше для более гибкого выбора инструментов
            "maxTokens": max_tokens,
        },
        "messages": messages,
    }
    
    resp = requests.post(settings.YANDEX_COMPLETION_URL, headers=headers, json=payload, timeout=90)
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        # Проброс подробностей ошибки от Yandex API
        try:
            error_detail = resp.json()
            error_message = str(e)
            if isinstance(error_detail, dict):
                if "message" in error_detail:
                    error_message += f": {error_detail['message']}"
                elif "error" in error_detail:
                    error_message += f": {error_detail['error']}"
        except Exception:
            error_message = f"{e}: {resp.text[:500]}"
        raise requests.HTTPError(error_message) from e
    
    data = resp.json()
    text = (
        data.get("result", {})
        .get("alternatives", [{}])[0]
        .get("message", {})
        .get("text", "")
        .strip()
    )
    return text


def agent_loop(user_query: str, table_hint: Optional[str] = None, max_iterations: int = 5) -> Dict[str, Any]:
    """
    Агентский цикл: обрабатывает запрос пользователя, выбирает и вызывает инструменты.
    
    Args:
        user_query: Запрос пользователя на естественном языке
        table_hint: Подсказка о таблице (опционально)
        max_iterations: Максимальное количество итераций агентского цикла
    
    Returns:
        Словарь с результатами: tool_calls, results, final_answer
    """
    tools = get_tools_definitions()
    schema_text = _build_schema_summary()
    tools_prompt = _build_tools_prompt(tools)
    
    # История разговора
    conversation_history: List[Dict[str, str]] = []
    
    # Результаты вызовов инструментов
    tool_results: List[Dict[str, Any]] = []
    
    # Максимальная длина истории (чтобы не превысить лимит токенов)
    MAX_HISTORY_LENGTH = 10
    def _select_relevant_results(all_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Не ограничиваем контекст: возвращаем все результаты инструментов,
        чтобы интерфейс и LLM видели полный ход рассуждений.
        Ошибки уже скрываются отдельно и в результаты не попадают.
        """
        return all_results

    
    # Системный промпт
    analysis_step = (
        "11. SQLite: НЕ используй DATE_FORMAT/DATE_TRUNC/EXTRACT/TO_CHAR — вместо этого для месяца используй strftime('%Y-%m', \"Дата\") "
        "или для dd.mm.YYYY — substr(\"Дата\",7,4)||'-'||substr(\"Дата\",4,2), с алиасом month и GROUP BY month.\n"
        "12. Используй ТОЛЬКО те имена колонок/таблиц, которые перечислены в схеме/превью, без переводов (например, 'Дата' ≠ 'Date'). ВСЕ идентификаторы должны быть в двойных кавычках.\n"
        "13. После получения данных переходи к аналитике (t_test/anova/plot/groupby). "
        "Финальный человеко‑понятный ответ будет сформирован автоматически на основе последних результатов; "
        "твоя задача — возвращать только JSON с вызовом следующего инструмента.\n"
    )
    if settings.LLM_ONLY_SQL:
        analysis_step = (
            "11. SQLite: НЕ используй DATE_FORMAT/DATE_TRUNC/EXTRACT/TO_CHAR — вместо этого для месяца используй strftime('%Y-%m', \"Дата\") "
            "или для dd.mm.YYYY — substr(\"Дата\",7,4)||'-'||substr(\"Дата\",4,2), с алиасом month и GROUP BY month.\n"
            "12. Используй ТОЛЬКО те имена колонок/таблиц, которые перечислены в схеме/превью, без переводов (например, 'Дата' ≠ 'Date'). ВСЕ идентификаторы должны быть в двойных кавычках.\n"
            "13. РЕЖИМ LLM_ONLY_SQL: используй ТОЛЬКО инструмент sql_query. Любой анализ выполняй с помощью одного или нескольких SQL‑запросов "
            "(SELECT/GROUP BY/CASE/CTE и т.п.). Всегда возвращай только JSON с вызовом sql_query.\n"
        )
    system_prompt = (
        "Ты интеллектуальный агент для анализа данных. "
        "Твоя задача - понимать запросы пользователя на естественном языке и выбирать подходящие инструменты для их выполнения.\n\n"
        f"{schema_text}\n\n"
        f"{tools_prompt}\n\n"
        "Инструкции:\n"
        "1. Проанализируй запрос пользователя и определи, какой инструмент нужен\n"
        "2. Если запрос требует получения данных из базы данных (любые SELECT запросы, подсчеты, фильтрация, группировка, сортировка, агрегация), "
        "используй инструмент 'sql_query' с параметром 'sql', содержащим валидный SQL SELECT запрос\n"
        "3. Для построения графиков используй инструмент 'plot' с указанием типа графика (bar, line, hist, scatter)\n"
        "4. Для статистических тестов (сравнение групп, ANOVA) используй соответствующие инструменты (t_test, anova)\n"
        "5. Для других операций с данными (группировка, сводные таблицы, объединение) используй соответствующие инструменты\n"
        "6. Верни ТОЛЬКО JSON в формате: {\"tool_name\": \"название_инструмента\", \"parameters\": {...}}\n"
        "7. Всегда извлекай названия таблиц и колонок из схемы БД, указанной выше\n"
        "8. Если запрос требует нескольких шагов, начни с первого инструмента\n"
        "9. Всегда заключай названия таблиц и колонок в двойные кавычки (\"...\") в SQL; "
        "это особенно важно для имён с пробелами, точками, символом %, кириллицей или начинающихся с цифры. "
        "Если имя таблицы начинается с цифры (например, 1810_2), обязательно используй FROM \"1810_2\".\n"
        "10. Для t_test: parameter group_col — это НАЗВАНИЕ КОЛОНКИ с категориями; "
        "parameters group_a и group_b — это КОНКРЕТНЫЕ ЗНАЧЕНИЯ из этой колонки, а не имена колонок. "
        "Для сравнения более чем двух групп используй параметр 'groups' (список значений) — будет выполнен попарный t‑тест. "
        "Если пользователь не указал группы, выбери две самые частые категории в group_col.\n"
        f"{analysis_step}"
    )
    
    conversation_history.append({"role": "system", "text": system_prompt})
    
    current_query = user_query
    if table_hint:
        current_query += f" (таблица: {table_hint})"
    
    last_sql_normalized: Optional[str] = None
    error_count: int = 0
    for iteration in range(max_iterations):
        # Добавляем запрос пользователя
        if iteration == 0:
            user_message = current_query
        else:
            # Ограничиваем длину истории, чтобы не превысить лимит токенов
            recent_results = tool_results[-2:]  # Только последние 2 результата
            results_summary = json.dumps(recent_results, ensure_ascii=False, default=str)[:1000]
            user_message = f"Продолжи выполнение задачи. Результаты предыдущих шагов: {results_summary}"
        
        conversation_history.append({"role": "user", "text": user_message})
        
        # Ограничиваем длину истории разговора (оставляем последние N сообщений)
        if len(conversation_history) > MAX_HISTORY_LENGTH:
            # Оставляем системный промпт и последние сообщения
            conversation_history[:] = [conversation_history[0]] + conversation_history[-(MAX_HISTORY_LENGTH-1):]
        
        # Вызываем LLM для выбора инструмента
        try:
            llm_response = _call_yandex_llm(conversation_history)
        except Exception as e:
            return {
                "error": f"Ошибка при вызове LLM: {str(e)}",
                "tool_calls": [{"tool_name": tc.tool_name, "parameters": tc.parameters} for tc in [ToolCall(tool_name="error", parameters={})]],
                "results": _select_relevant_results(tool_results)
            }
        
        # Парсим вызов инструмента
        tool_call = _parse_tool_call_from_llm(llm_response)
        
        if not tool_call:
            # Если не удалось распарсить, пытаемся понять, нужен ли еще инструмент или можно завершить
            if "none" in llm_response.lower() or "заверш" in llm_response.lower() or "готов" in llm_response.lower():
                # Генерируем финальный ответ
                final_answer = _generate_final_answer(user_query, tool_results, llm_response)
                return {
                    "tool_calls": [{"tool_name": tc.tool_name, "parameters": tc.parameters} for tc in [ToolCall(tool_name="none", parameters={})]],
                    "results": _select_relevant_results(tool_results),
                    "final_answer": final_answer,
                    "llm_response": llm_response
                }
            # Продолжаем попытку
            conversation_history.append({"role": "assistant", "text": llm_response})
            continue
        
        if tool_call.tool_name == "none":
            # Генерируем финальный ответ
            final_answer = _generate_final_answer(user_query, tool_results, llm_response)
            return {
                "tool_calls": [tc.dict() for tc in [ToolCall(tool_name="none", parameters={})]],
                "results": _select_relevant_results(tool_results),
                "final_answer": final_answer,
                "llm_response": llm_response
            }
        
        # Вызываем инструмент
        # Если LLM выбрала запрещённый инструмент в режиме LLM_ONLY_SQL — даём подсказку и продолжаем итерацию без ошибки
        if settings.LLM_ONLY_SQL and tool_call.tool_name != "sql_query":
            conversation_history.append({
                "role": "assistant",
                "text": (
                    "РЕЖИМ LLM_ONLY_SQL: используй ТОЛЬКО sql_query. "
                    "Сформируй корректный SQL под SQLite, учитывая имена колонок из схемы/превью и кавычки."
                )
            })
            continue
        try:
            result = call_tool(tool_call)
            tool_results.append({
                "tool_name": tool_call.tool_name,
                "parameters": tool_call.parameters,
                "result": result
            })
            
            # Добавляем результат в историю (ограничиваем длину)
            if "rows" in result and isinstance(result["rows"], list):
                # Для больших результатов показываем только краткую сводку
                rows_count = len(result["rows"])
                result_summary = f"Получено {rows_count} строк"
                if rows_count > 0 and len(result["rows"]) > 0:
                    # Показываем пример первой строки
                    first_row_keys = list(result["rows"][0].keys())[:5]
                    result_summary += f", колонки: {', '.join(first_row_keys)}"
            else:
                result_summary = json.dumps(result, ensure_ascii=False, default=str)[:300]
            
            conversation_history.append({
                "role": "assistant",
                "text": f"Вызван инструмент {tool_call.tool_name}. {result_summary}"
            })
            
            # Если результат содержит финальный ответ (например, график), можем завершить
            if tool_call.tool_name == "plot" and "image_base64" in result:
                final_answer = _generate_final_answer(user_query, tool_results, "")
                return {
                    "tool_calls": [{"tool_name": tool_call.tool_name, "parameters": tool_call.parameters}],
                    "results": _select_relevant_results(tool_results),
                    "final_answer": final_answer
                }
            
            # Анти-дубликат для sql_query и подсказка перейти к анализу
            if tool_call.tool_name == "sql_query":
                sql_param = tool_call.parameters.get("sql", "")
                # Нормализуем SQL: убираем лишние пробелы
                normalized = " ".join(str(sql_param).split())
                if last_sql_normalized is not None and normalized == last_sql_normalized:
                    # Подсказка LLM: не повторяй тот же SELECT, перейди к анализу
                    if settings.LLM_ONLY_SQL:
                        conversation_history.append({
                            "role": "assistant",
                            "text": (
                                "Данные уже получены этим же SELECT. Не повторяй sql_query. "
                                "Сформируй следующий sql_query с необходимой агрегацией/фильтрацией/группировкой, "
                                "чтобы ответить на задачу. Верни ТОЛЬКО JSON с инструментом sql_query."
                            )
                        })
                    else:
                        conversation_history.append({
                            "role": "assistant",
                            "text": (
                                "Данные уже получены этим же SELECT. Не повторяй sql_query. "
                                "Сейчас выбери аналитический инструмент в зависимости от запроса: "
                                "t_test (если сравнение групп; если группы не указаны — возьми две самые частые категории), "
                                "anova (если групп > 2), plot (если просили визуализацию), "
                                "groupby (для агрегации). Верни только JSON с инструментом."
                            )
                        })
                else:
                    last_sql_normalized = normalized
                    # Мягкая подсказка после получения данных: перейти к аналитике/визуализации
                    if settings.LLM_ONLY_SQL:
                        conversation_history.append({
                            "role": "assistant",
                            "text": (
                                "Данные получены. Теперь выполни следующий шаг анализа через SQL: "
                                "сформируй новый sql_query (агрегации, фильтры, группировки) в соответствии с задачей. "
                                "Верни ТОЛЬКО JSON с инструментом sql_query."
                            )
                        })
                    else:
                        conversation_history.append({
                            "role": "assistant",
                            "text": (
                                "Данные получены. Теперь выполни следующий шаг анализа согласно задаче: "
                                "выбери один из инструментов t_test/anova/plot/groupby и верни JSON вызова."
                            )
                        })
            
        except Exception as e:
            error_msg = f"Ошибка при вызове инструмента {tool_call.tool_name}: {str(e)}"
            # Логируем ошибку, но НЕ показываем её в интерфейсе (не добавляем в results)
            logging.exception(error_msg)
            # Даем LLM точную обратную связь с контекстом схемы и правилами кавычек/дат для SQLite
            guidance = error_msg
            if tool_call.tool_name == "sql_query":
                sql_param = str(tool_call.parameters.get("sql", ""))
                # Пытаемся извлечь имя таблицы из FROM
                tbl = None
                m = re.search(r'FROM\s+("([^"]+)"|([^\s;]+))', sql_param, flags=re.IGNORECASE)
                if m:
                    tbl = (m.group(2) or m.group(3) or "").strip().strip('"')
                schema_hint = _build_schema_summary()
                if tbl:
                    try:
                        cols = db.get_table_columns(tbl)
                        col_list = ", ".join([f"\"{c.get('name')}\"" for c in cols])
                        tbl_hint = f'Таблица "{tbl}": допустимые колонки: {col_list}'
                    except Exception:
                        tbl_hint = ""
                else:
                    tbl_hint = ""
                rules = (
                    "Правила для SQLite: ВСЕ имена таблиц/колонок в двойных кавычках; "
                    "не используй DATE_FORMAT/DATE_TRUNC/EXTRACT/TO_CHAR; "
                    "месяц: substr(\"Дата\",1,7) или strftime('%Y-%m', \"Дата\"); "
                    "используй ровно имена из схемы/превью (не 'Date', если есть 'Дата')."
                )
                guidance = (
                    f"{error_msg}\n{tbl_hint}\n{rules}\n"
                    "Исправь SQL и верни ТОЛЬКО JSON следующего вызова инструмента sql_query."
                )
            conversation_history.append({"role": "assistant", "text": guidance})
            error_count += 1
            # Если слишком много ошибок подряд, прекращаем
            if error_count >= 3:
                final_answer = _generate_final_answer(user_query, tool_results, "")
                return {
                    "tool_calls": [{"tool_name": tool_call.tool_name, "parameters": tool_call.parameters}],
                    "results": _select_relevant_results(tool_results),
                    "final_answer": final_answer
                }
            # Продолжаем, возможно агент выберет другой инструмент
    
    # Если достигли максимума итераций, генерируем финальный ответ
    final_answer = _generate_final_answer(user_query, tool_results, "")
    return {
        "tool_calls": [{"tool_name": tc.tool_name, "parameters": tc.parameters} for tc in [ToolCall(tool_name="max_iterations", parameters={})]],
        "results": _select_relevant_results(tool_results),
        "final_answer": final_answer
    }


def _generate_final_answer(
    user_query: str,
    tool_results: List[Dict[str, Any]],
    llm_response: str
) -> str:
    """Генерирует финальный ответ на основе результатов инструментов"""
    if not tool_results:
        return "Не удалось выполнить запрос. Попробуйте переформулировать."
    
    # Отметим, что был построен график (для включения в ответ)
    plot_present = any(
        tr.get("tool_name") == "plot" and "result" in tr and "image_base64" in tr["result"]
        for tr in tool_results
    )
    # Формируем краткое описание результатов с приоритизацией аналитики
    summary_parts = []
    ttest_part = ""
    anova_part = ""
    # Попробуем подготовить резюме по последнему релевантному табличному результату
    llm_summary: str | None = None
    last_sql: str | None = None
    last_rows: List[Dict[str, Any]] | None = None
    last_context_sql: str | None = None
    # Выбор базовых данных: groupby → filter → sql_query (исключаем plot/прочее)
    def _pick_base_rows(trs: List[Dict[str, Any]]) -> None:
        nonlocal last_rows, last_sql, last_context_sql
        # groupby
        for tr in reversed(trs):
            if tr.get("tool_name") == "groupby" and tr.get("result", {}).get("rows"):
                params = tr.get("parameters") or {}
                rows = tr["result"]["rows"]
                table = params.get("table")
                gb = params.get("group_by")
                aggs = params.get("aggregations")
                having = params.get("having")
                order_by = params.get("order_by")
                limit = params.get("limit")
                sel_aggs: List[str] = []
                if isinstance(aggs, dict):
                    for col, fn in aggs.items():
                        sel_aggs.append(f"{str(fn).upper()}({col}) AS {str(fn).lower()}_{col}")
                gb_str = ", ".join(gb) if isinstance(gb, list) else str(gb or "")
                sel = ", ".join(([gb_str] if gb_str else []) + sel_aggs) or "*"
                ctx = f"SELECT {sel} FROM {table or '<table>'}"
                if gb_str:
                    ctx += f" GROUP BY {gb_str}"
                if having:
                    ctx += f" HAVING {having}"
                if order_by:
                    ctx += f" ORDER BY {order_by}"
                if limit:
                    ctx += f" LIMIT {limit}"
                last_rows = rows
                last_context_sql = ctx
                return
        # filter
        for tr in reversed(trs):
            if tr.get("tool_name") == "filter" and tr.get("result", {}).get("rows"):
                params = tr.get("parameters") or {}
                rows = tr["result"]["rows"]
                table = params.get("table")
                where = params.get("where")
                order_by = params.get("order_by")
                limit = params.get("limit")
                ctx = f"SELECT * FROM {table or '<table>'}"
                if where:
                    ctx += f" WHERE {where}"
                if order_by:
                    ctx += f" ORDER BY {order_by}"
                if limit:
                    ctx += f" LIMIT {limit}"
                last_rows = rows
                last_context_sql = ctx
                return
        # sql_query
        for tr in reversed(trs):
            if tr.get("tool_name") == "sql_query" and tr.get("result", {}).get("rows"):
                params = tr.get("parameters") or {}
                rows = tr["result"]["rows"]
                last_rows = rows
                last_sql = params.get("sql")
                return
        return
    _pick_base_rows(tool_results)
    for tr in tool_results:
        tool_name = tr.get("tool_name", "unknown")
        if "error" in tr:
            summary_parts.append(f"Ошибка в {tool_name}: {tr['error']}")
        elif "result" in tr:
            result = tr["result"]
            if "rows" in result:
                cnt = result.get('count', len(result.get('rows', [])))
                # Покажем компактный состав колонок, если есть строки
                cols = []
                rows = result.get("rows", [])
                if rows:
                    cols = list(rows[0].keys())
                summary_parts.append(
                    f"{tool_name}: получено {cnt} строк" + (f", колонки: {', '.join(cols[:6])}" if cols else "")
                )
            elif "comparisons" in result and isinstance(result["comparisons"], list):
                comps = result["comparisons"]
                sig = [c for c in comps if float(c.get("p_value_adj_bonferroni", c.get("p_value", 1.0))) < 0.05]
                top = sorted(comps, key=lambda c: abs(float(c.get("diff", 0.0))), reverse=True)[:3]
                parts = []
                for c in top:
                    parts.append(f"{c.get('group_a')} vs {c.get('group_b')}: Δ={float(c.get('diff', 0.0)):+.2f}, p={float(c.get('p_value', 1.0)):.4f}")
                summary_parts.append(
                    "t-тест (попарные сравнения): "
                    + (f"значимых сравнений: {len(sig)}; " if sig else "значимых сравнений нет; ")
                    + ("; ".join(parts) if parts else "нет результатов")
                )
            elif "t_stat" in result:
                p = float(result.get('p_value', 1.0))
                t_val = float(result.get('t_stat', 0.0))
                mean_a = float(result.get('mean_a', 0.0))
                mean_b = float(result.get('mean_b', 0.0))
                n_a = int(result.get('n_a', 0))
                n_b = int(result.get('n_b', 0))
                signif = "есть статистически значимое различие (p<0.05)" if p < 0.05 else "статистически значимое различие не обнаружено (p≥0.05)"
                # Извлечём контекст из параметров
                params = tr.get("parameters") or {}
                group_col = params.get("group_col", "group")
                value_col = params.get("value_col", "value")
                group_a = params.get("group_a", "A")
                group_b = params.get("group_b", "B")
                if result.get("auto_groups_used") and result.get("chosen_groups"):
                    chosen = result.get("chosen_groups") or []
                    if isinstance(chosen, list) and len(chosen) >= 2:
                        group_a, group_b = str(chosen[0]), str(chosen[1])
                equal_var_flag = params.get("equal_var", False)
                test_name = "t-тест (Welch)" if not equal_var_flag else "t-тест (равные дисперсии)"
                diff = mean_b - mean_a
                ttest_part = (
                    f"{test_name}: сравнение '{value_col}' по группам '{group_a}' и '{group_b}' (столбец групп '{group_col}'). "
                    f"Средние: {mean_a:.2f} (n={n_a}) vs {mean_b:.2f} (n={n_b}), разница = {diff:+.2f}. "
                    f"t={t_val:.3f}, p={p:.4f} → {signif} при α=0.05."
                )
            elif "f_stat" in result:
                p = float(result.get('p_value', 1.0))
                signif = "значимо (p<0.05)" if p < 0.05 else "не значимо (p≥0.05)"
                anova_part = f"ANOVA: F={result.get('f_stat', 0):.3f}, p={p:.4f} → {signif}"
            else:
                summary_parts.append(f"{tool_name}: выполнено успешно")
    
    # Если есть статистика, выносим её в начало
    stats_parts = []
    if ttest_part:
        stats_parts.append(ttest_part)
    if anova_part:
        stats_parts.append(anova_part)
    if plot_present:
        stats_parts.append("График построен успешно.")
    ordered = stats_parts + summary_parts
    
    # Попробуем получить человеко-понятное резюме от LLM по последнему табличному результату
    if last_rows and (last_sql or last_context_sql):
        try:
            llm_summary = generate_answer_from_rows(user_query, last_sql or last_context_sql or "", last_rows)
        except Exception:
            llm_summary = None
    # Детерминированное резюме для типичных groupby/agg результатов (во избежание неверных чисел в LLM-ответе)
    def _build_structured_summary(rows: List[Dict[str, Any]]) -> Optional[str]:
        def to_float(val: Any) -> Optional[float]:
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, str):
                s = val.strip()
                if not s:
                    return None
                # убираем пробелы-разделители тысяч (включая неразрывные)
                s = s.replace(" ", "").replace("\u00A0", "")
                # заменяем запятую на точку
                s = s.replace(",", ".")
                # убираем знак процента
                if s.endswith("%"):
                    s = s[:-1]
                try:
                    return float(s)
                except Exception:
                    return None
            return None
        if not rows:
            return None
        first = rows[0]
        if not isinstance(first, dict):
            return None
        keys = list(first.keys())
        if not keys:
            return None
        # Определяем числовые и текстовые колонки (сканируем несколько строк, чтобы распознать строки-числа)
        numeric_cols: List[str] = []
        text_cols: List[str] = []
        scan_n = min(50, len(rows))
        for k in keys:
            saw_num = False
            saw_text = False
            for i in range(scan_n):
                v = rows[i].get(k)
                if to_float(v) is not None:
                    saw_num = True
                    break
                if isinstance(v, str):
                    saw_text = True
            if saw_num:
                numeric_cols.append(k)
            elif saw_text:
                text_cols.append(k)
        if not numeric_cols:
            return None
        # Выбираем приоритетную числовую колонку
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
        num_col = sorted(numeric_cols, key=lambda c: (-score_num(c), c))[0]
        # Выбираем приоритетную текстовую колонку (например category)
        def score_text(name: str) -> int:
            n = name.lower()
            score = 0
            if "category" in n or "катег" in n:
                score += 3
            if "name" in n or "type" in n or "group" in n:
                score += 2
            if n in ("x",):
                score += 1
            return score
        text_col = None
        if text_cols:
            text_col = sorted(text_cols, key=lambda c: (-score_text(c), c))[0]
        def fmt_num(v: Any) -> str:
            fv = to_float(v)
            return f"{fv:.2f}" if fv is not None else str(v)
        # Ветка 1: есть категориальная колонка — делаем топ и сводку
        if text_col:
            try:
                sorted_rows = sorted(rows, key=lambda r: (to_float(r.get(num_col)) or 0.0), reverse=True)
            except Exception:
                return None
            top_n = min(5, len(sorted_rows))
            top = sorted_rows[:top_n]
            if not top:
                return None
            bullets = []
            for r in top:
                label = r.get(text_col) if text_col else ""
                bullets.append(f"- {label or num_col} — {fmt_num(r.get(num_col))}")
            min_row = sorted_rows[-1]
            max_row = sorted_rows[0]
            min_label = min_row.get(text_col) if text_col else num_col
            max_label = max_row.get(text_col) if text_col else num_col
            min_v = fmt_num(min_row.get(num_col))
            max_v = fmt_num(max_row.get(num_col))
            total = sum((to_float(r.get(num_col)) or 0.0) for r in rows)
            mean_over = total / max(1, len(rows))
            title_metric = num_col
            if "avg" in num_col.lower() or "mean" in num_col.lower():
                title_metric = "среднее значение"
            short_itog = f"Короткий итог: лидирует {max_label} — {max_v}; среднее по всем — {mean_over:.2f}."
            meaning = "Что это значит: одна или несколько категорий заметно выделяются по значению метрики."
            header = f"Ключевые цифры — топ-{top_n} по '{title_metric}':"
            overview = f"Максимум: {max_label} — {max_v}; минимум: {min_label} — {min_v}; среднее по всем: {mean_over:.2f}."
            rec = "Рекомендации: сфокусируйтесь на лидирующих/отстающих категориях или постройте график для наглядности."
            return short_itog + "\n" + meaning + "\n" + header + "\n" + "\n".join(bullets) + "\n" + overview + "\n" + rec
        # Ветка 2: только числовые колонки — считаем агрегаты по каждой
        n = len(rows)
        per_col_stats: Dict[str, Dict[str, float]] = {}
        for col in numeric_cols:
            vals: List[float] = []
            for r in rows:
                fv = to_float(r.get(col))
                if fv is not None:
                    vals.append(fv)
            if not vals:
                continue
            vals_sorted = sorted(vals)
            cnt = len(vals_sorted)
            mean_v = sum(vals_sorted) / max(1, cnt)
            min_v = vals_sorted[0]
            max_v = vals_sorted[-1]
            # Квантили (простая дискретная оценка)
            q1 = vals_sorted[int(0.25 * (cnt - 1))]
            med = vals_sorted[int(0.50 * (cnt - 1))]
            q3 = vals_sorted[int(0.75 * (cnt - 1))]
            # Стандартное отклонение (population)
            var = sum((x - mean_v) ** 2 for x in vals_sorted) / max(1, cnt)
            std = math.sqrt(var)
            per_col_stats[col] = {
                "count": float(cnt),
                "mean": mean_v,
                "min": min_v,
                "max": max_v,
                "q1": q1,
                "median": med,
                "q3": q3,
                "std": std,
            }
        if per_col_stats:
            # Короткий итог по первым двум метрикам
            head_cols = numeric_cols[:2]
            short_bits: List[str] = []
            for c in head_cols:
                s = per_col_stats.get(c)
                if s:
                    short_bits.append(f"{c} ≈ {s['mean']:.2f} (диапазон {s['min']:.2f}-{s['max']:.2f})")
            short_line = "; ".join(short_bits)
            # Ключевые показатели по всем метрикам
            lines: List[str] = []
            for col in numeric_cols:
                s = per_col_stats.get(col)
                if not s:
                    continue
                lines.append(
                    f"- {col}: среднее {s['mean']:.2f}, медиана {s['median']:.2f}, "
                    f"Q1 {s['q1']:.2f}, Q3 {s['q3']:.2f}, мин {s['min']:.2f}, макс {s['max']:.2f}, "
                    f"ст. отклонение {s['std']:.2f}"
                )
            # Сравнение первых двух числовых столбцов (если есть 2+)
            compare_lines: List[str] = []
            if len(numeric_cols) >= 2:
                a, b = numeric_cols[0], numeric_cols[1]
                pairs: List[tuple[float, float]] = []
                gt_count = 0
                for r in rows:
                    fa = to_float(r.get(a))
                    fb = to_float(r.get(b))
                    if fa is not None and fb is not None:
                        pairs.append((fa, fb))
                        if fa > fb:
                            gt_count += 1
                m = len(pairs)
                if m >= 2:
                    mean_a = sum(x for x, _ in pairs) / m
                    mean_b = sum(y for _, y in pairs) / m
                    diff_mean = mean_a - mean_b
                    # Корреляция Пирсона
                    var_a = sum((x - mean_a) ** 2 for x, _ in pairs) / m
                    var_b = sum((y - mean_b) ** 2 for _, y in pairs) / m
                    std_a = math.sqrt(var_a)
                    std_b = math.sqrt(var_b)
                    cov = sum((x - mean_a) * (y - mean_b) for x, y in pairs) / m
                    corr = cov / (std_a * std_b) if std_a > 0 and std_b > 0 else 0.0
                    share_gt = 100.0 * gt_count / m
                    compare_lines.append(
                        f"- {a} vs {b}: средняя разница {diff_mean:+.2f} ({a}−{b}), "
                        f"{share_gt:.1f}% строк, где {a}>{b}, корреляция r={corr:.2f}"
                    )
            # Рекомендации (общие, ненавязчивые)
            rec_line = "- Для ясности разбейте данные по времени/категориям или постройте график, чтобы увидеть тренды."
            parts: List[str] = []
            parts.append(f"Короткий итог: в выборке {n} строк. {short_line}." if short_line else f"Короткий итог: в выборке {n} строк.")
            parts.append("Что это значит: значения распределены в указанных диапазонах; ориентируйтесь на средние и разброс.")
            parts.append("Ключевые показатели:\n" + "\n".join(lines))
            if compare_lines:
                parts.append("Сравнение показателей:\n" + "\n".join(compare_lines))
            parts.append("Рекомендации:\n" + rec_line)
            return "\n".join(parts)
        return None
    structured_summary = _build_structured_summary(last_rows or [])
    if structured_summary:
        ordered = [structured_summary] + ordered
    elif llm_summary:
        ordered = [llm_summary] + ordered
    else:
        # Простейший fallback: короткий, понятный предварительный обзор по данным
        def _build_simple_preview(rows: List[Dict[str, Any]]) -> Optional[str]:
            if not rows:
                return None
            keys = list(rows[0].keys())
            if not keys:
                return None
            n = len(rows)
            samples = rows[:3]
            lines = []
            for r in samples:
                # Формируем «ключ: значение» по первой паре/двум, чтобы было понятно «как для ребёнка»
                pair_items = []
                for k in keys[:2]:
                    pair_items.append(f"{k}: {r.get(k)}")
                lines.append(" · " + "; ".join(pair_items))
            return "Короткий обзор по данным:\n" + f"Всего строк: {n}.\nПримеры первых значений:\n" + "\n".join(lines)
        simple_preview = _build_simple_preview(last_rows or [])
        if simple_preview:
            ordered = [simple_preview] + ordered
    
    if llm_response and len(llm_response) > 50:
        return llm_response + "\n\n" + "\n".join(ordered)
    
    return "\n".join(ordered) if ordered else "Запрос выполнен."

