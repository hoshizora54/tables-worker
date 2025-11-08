"""
Агентский сервис с поддержкой Tool Use.
Реализует агентский цикл: LLM анализирует запрос, выбирает инструменты и вызывает их.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import json
import re

import requests

from backend.core.config import settings
from backend.core import db
from backend.services.tool_service import (
    get_tools_definitions,
    call_tool,
    ToolCall,
    ToolDefinition
)


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
    
    # Системный промпт
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
    )
    
    conversation_history.append({"role": "system", "text": system_prompt})
    
    current_query = user_query
    if table_hint:
        current_query += f" (таблица: {table_hint})"
    
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
                "results": tool_results
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
                    "results": tool_results,
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
                "results": tool_results,
                "final_answer": final_answer,
                "llm_response": llm_response
            }
        
        # Вызываем инструмент
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
                    "results": tool_results,
                    "final_answer": final_answer
                }
            
            # Если инструмент успешно выполнен и вернул данные, можем завершить
            if tool_call.tool_name == "sql_query" and result.get("rows"):
                # SQL запрос выполнен успешно
                if iteration >= 1:  # Если это не первая итерация, можем завершить
                    final_answer = _generate_final_answer(user_query, tool_results, "")
                    return {
                        "tool_calls": [{"tool_name": tool_call.tool_name, "parameters": tool_call.parameters}],
                        "results": tool_results,
                        "final_answer": final_answer
                    }
            
        except Exception as e:
            error_msg = f"Ошибка при вызове инструмента {tool_call.tool_name}: {str(e)}"
            tool_results.append({
                "tool_name": tool_call.tool_name,
                "parameters": tool_call.parameters,
                "error": str(e)
            })
            conversation_history.append({
                "role": "assistant",
                "text": error_msg + ". Попробуй использовать другой инструмент или другие параметры."
            })
            # Если слишком много ошибок подряд, прекращаем
            if len([r for r in tool_results if "error" in r]) >= 3:
                final_answer = _generate_final_answer(user_query, tool_results, "")
                return {
                    "tool_calls": [{"tool_name": tool_call.tool_name, "parameters": tool_call.parameters}],
                    "results": tool_results,
                    "final_answer": final_answer
                }
            # Продолжаем, возможно агент выберет другой инструмент
    
    # Если достигли максимума итераций, генерируем финальный ответ
    final_answer = _generate_final_answer(user_query, tool_results, "")
    return {
        "tool_calls": [{"tool_name": tc.tool_name, "parameters": tc.parameters} for tc in [ToolCall(tool_name="max_iterations", parameters={})]],
        "results": tool_results,
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
    
    # Если есть график, возвращаем специальный ответ
    for tr in tool_results:
        if tr.get("tool_name") == "plot" and "result" in tr and "image_base64" in tr["result"]:
            return "График построен успешно."
    
    # Формируем краткое описание результатов
    summary_parts = []
    for tr in tool_results:
        tool_name = tr.get("tool_name", "unknown")
        if "error" in tr:
            summary_parts.append(f"Ошибка в {tool_name}: {tr['error']}")
        elif "result" in tr:
            result = tr["result"]
            if "rows" in result:
                summary_parts.append(f"{tool_name}: получено {result.get('count', len(result.get('rows', [])))} строк")
            elif "t_stat" in result:
                summary_parts.append(
                    f"t-тест: t={result.get('t_stat', 0):.3f}, p={result.get('p_value', 0):.4f}, "
                    f"средние: {result.get('mean_a', 0):.2f} vs {result.get('mean_b', 0):.2f}"
                )
            elif "f_stat" in result:
                summary_parts.append(
                    f"ANOVA: F={result.get('f_stat', 0):.3f}, p={result.get('p_value', 0):.4f}"
                )
            else:
                summary_parts.append(f"{tool_name}: выполнено успешно")
    
    if llm_response and len(llm_response) > 50:
        return llm_response + "\n\n" + "\n".join(summary_parts)
    
    return "\n".join(summary_parts) if summary_parts else "Запрос выполнен."

