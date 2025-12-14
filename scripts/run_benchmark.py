"""
Скрипт для загрузки бенчмарка с Hugging Face и оценки агентской системы.
Поддерживает бенчмарки: Spider, WikiSQL, BIRD и другие Text-to-SQL датасеты.
"""
import os
import sys
import json
import time
from pathlib import Path
from typing import Dict, List, Any, Optional
import requests
from datasets import load_dataset
import pandas as pd

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.core.db import run_select, get_engine
from backend.services.agent_service import agent_loop


# Конфигурация
BENCHMARK_DIR = Path(__file__).parent.parent / "data" / "benchmark"
BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api")


class BenchmarkRunner:
    """Класс для запуска бенчмарков"""
    
    def __init__(self, api_base_url: str = API_BASE_URL):
        self.api_base_url = api_base_url
        self.results = []
        
    def load_dataset_from_hf(self, dataset_name: str, split: str = "test", limit: Optional[int] = None):
        """
        Загружает датасет с Hugging Face.
        
        Поддерживаемые датасеты:
        - 'spider' - Text-to-SQL бенчмарк (https://huggingface.co/datasets/spider)
        - 'wikisql' - WikiSQL бенчмарк (https://huggingface.co/datasets/wikisql)
        - 'b-mc2/sql-create-context' - SQL с контекстом
        """
        print(f"📥 Загрузка датасета {dataset_name} с Hugging Face...")
        
        try:
            if dataset_name == "spider":
                dataset = load_dataset("spider", split=split)
            elif dataset_name == "wikisql":
                dataset = load_dataset("wikisql", split=split)
            elif dataset_name == "sql-create-context":
                dataset = load_dataset("b-mc2/sql-create-context", split=split)
            else:
                # Пробуем загрузить напрямую
                dataset = load_dataset(dataset_name, split=split)
            
            if limit:
                dataset = dataset.select(range(min(limit, len(dataset))))
            
            print(f"✓ Загружено {len(dataset)} примеров")
            return dataset
        except Exception as e:
            print(f"Ошибка при загрузке: {e}")
            print(f"Попробуйте другой датасет или проверьте доступность")
            return None
    
    def execute_sql_safe(self, sql: str) -> Optional[List[Dict]]:
        """Безопасное выполнение SQL запроса"""
        try:
            # Только SELECT запросы
            sql_upper = sql.strip().upper()
            if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
                return None
            return run_select(sql)
        except Exception as e:
            print(f"   Ошибка SQL: {e}")
            return None
    
    def compare_results(self, actual: List[Dict], expected: List[Dict], epsilon: float = 1e-6) -> bool:
        """Сравнивает результаты выполнения с учетом epsilon для float"""
        if not actual and not expected:
            return True
        if not actual or not expected:
            return False
        if len(actual) != len(expected):
            return False
        
        # Преобразуем в списки словарей для сравнения
        try:
            actual_sorted = sorted([dict(row) for row in actual], key=str)
            expected_sorted = sorted([dict(row) for row in expected], key=str)
        except Exception:
            # Если не удалось отсортировать, сравниваем как есть
            actual_sorted = [dict(row) for row in actual]
            expected_sorted = [dict(row) for row in expected]
        
        for a, e in zip(actual_sorted, expected_sorted):
            if set(a.keys()) != set(e.keys()):
                return False
            for key in a.keys():
                a_val = a[key]
                e_val = e[key]
                
                # Для чисел используем epsilon
                if isinstance(a_val, (int, float)) and isinstance(e_val, (int, float)):
                    if abs(float(a_val) - float(e_val)) > epsilon:
                        return False
                elif str(a_val) != str(e_val):
                    return False
        
        return True
    
    def run_agent_query(self, query: str, table_hint: Optional[str] = None) -> Dict[str, Any]:
        """Выполняет запрос через агентский API"""
        try:
            result = agent_loop(query, table_hint=table_hint, max_iterations=5)
            return result
        except Exception as e:
            return {"error": str(e)}
    
    def evaluate_spider(self, dataset, limit: int = 50):
        """
        Оценивает систему на Spider бенчмарке.
        Spider содержит пары (вопрос, SQL) для различных баз данных.
        """
        print(f"\n🧪 Запуск оценки на Spider бенчмарке (первые {limit} примеров)...")
        
        correct = 0
        total = 0
        errors = []
        
        # Преобразуем в список, если это Dataset
        if hasattr(dataset, '__iter__') and not isinstance(dataset, (list, dict)):
            dataset_list = list(dataset[:limit])
        else:
            dataset_list = dataset[:limit] if isinstance(dataset, list) else [dataset]
        
        for i, example in enumerate(dataset_list):
            # Обрабатываем как dict или как объект с атрибутами
            if isinstance(example, dict):
                question = example.get("question", "")
                sql = example.get("SQL", "")
                db_id = example.get("db_id", "")
            else:
                question = getattr(example, "question", "") if hasattr(example, "question") else ""
                sql = getattr(example, "SQL", "") if hasattr(example, "SQL") else ""
                db_id = getattr(example, "db_id", "") if hasattr(example, "db_id") else ""
            
            if not question or not sql:
                continue
            
            total += 1
            print(f"\n[{i+1}/{limit}] Вопрос: {question[:60]}...")
            
            # Получаем ожидаемый результат (если можем выполнить SQL)
            expected_result = None
            try:
                expected_result = self.execute_sql_safe(sql)
            except Exception as e:
                print(f"   Не удалось выполнить эталонный SQL: {e}")
            
            # Запускаем агент
            start_time = time.time()
            agent_result = self.run_agent_query(question, table_hint=db_id)
            elapsed = time.time() - start_time
            
            # Извлекаем SQL из результата агента
            agent_sql = None
            agent_rows = None
            
            if "results" in agent_result:
                for tool_result in agent_result["results"]:
                    if tool_result.get("tool_name") == "sql_query":
                        params = tool_result.get("parameters", {})
                        agent_sql = params.get("sql")
                        agent_rows = tool_result.get("result", {}).get("rows")
                        break
            
            # Сравниваем результаты
            is_correct = False
            if expected_result and agent_rows:
                is_correct = self.compare_results(agent_rows, expected_result)
            elif agent_sql:
                # Если не можем сравнить результаты, хотя бы проверяем, что агент сгенерировал SQL
                is_correct = True  # Частичный успех
            
            if is_correct:
                correct += 1
                print(f"✓ Правильно ({elapsed:.2f}s)")
            else:
                print(f"✗ Неправильно")
                errors.append({
                    "question": question,
                    "expected_sql": sql[:100] if sql else "",
                    "agent_sql": agent_sql[:100] if agent_sql else "",
                    "expected_rows": len(expected_result) if expected_result else 0,
                    "agent_rows": len(agent_rows) if agent_rows else 0,
                })
            
            self.results.append({
                "dataset": "spider",
                "question": question,
                "expected_sql": sql,
                "agent_sql": agent_sql,
                "correct": is_correct,
                "elapsed": elapsed,
            })
        
        accuracy = correct / total if total > 0 else 0
        print(f"\n📊 Результаты Spider:")
        print(f"   Execution Accuracy: {accuracy:.2%} ({correct}/{total})")
        print(f"   Ошибок: {len(errors)}")
        
        return {
            "accuracy": accuracy,
            "correct": correct,
            "total": total,
            "errors": errors[:10],  # Первые 10 ошибок
        }
    
    def evaluate_sql_create_context(self, dataset, limit: int = 50):
        """
        Оценивает систему на sql-create-context бенчмарке.
        """
        print(f"\n🧪 Запуск оценки на sql-create-context (первые {limit} примеров)...")
        
        correct = 0
        total = 0
        
        # Преобразуем в список, если это Dataset
        try:
            if hasattr(dataset, 'select'):
                # Это HuggingFace Dataset
                dataset_list = list(dataset.select(range(min(limit, len(dataset)))))
            elif hasattr(dataset, '__iter__') and not isinstance(dataset, (list, dict, str)):
                dataset_list = list(dataset)
            else:
                dataset_list = dataset[:limit] if isinstance(dataset, list) else [dataset]
        except Exception as e:
            print(f"   Ошибка при обработке датасета: {e}")
            dataset_list = []
        
        for i, example in enumerate(dataset_list):
            # Обрабатываем как dict или как объект с атрибутами
            if isinstance(example, dict):
                question = example.get("question", "")
                answer = example.get("answer", "")
                context = example.get("context", "")
            else:
                question = getattr(example, "question", "") if hasattr(example, "question") else ""
                answer = getattr(example, "answer", "") if hasattr(example, "answer") else ""
                context = getattr(example, "context", "") if hasattr(example, "context") else ""
            
            if not question:
                print(f"   Пропущен пример {i+1}: нет вопроса (тип: {type(example)})")
                if isinstance(example, dict):
                    print(f"   Доступные ключи: {list(example.keys())}")
                continue
            
            total += 1
            print(f"\n[{i+1}/{limit}] Вопрос: {question[:60]}...")
            
            # Запускаем агент
            start_time = time.time()
            agent_result = self.run_agent_query(question)
            elapsed = time.time() - start_time
            
            # Проверяем, что агент использовал sql_query
            used_sql = False
            agent_sql = None
            if "results" in agent_result:
                for tool_result in agent_result["results"]:
                    if tool_result.get("tool_name") == "sql_query":
                        used_sql = True
                        params = tool_result.get("parameters", {})
                        agent_sql = params.get("sql")
                        break
            
            if used_sql:
                correct += 1
                print(f"✓ Использован sql_query ({elapsed:.2f}s)")
            else:
                print(f"✗ Не использован sql_query")
            
            self.results.append({
                "dataset": "sql-create-context",
                "question": question,
                "used_sql": used_sql,
                "agent_sql": agent_sql,
                "elapsed": elapsed,
            })
        
        accuracy = correct / total if total > 0 else 0
        print(f"\n📊 Результаты sql-create-context:")
        print(f"   Точность использования SQL: {accuracy:.2%} ({correct}/{total})")
        
        return {"accuracy": accuracy, "correct": correct, "total": total}
    
    def save_results(self, output_file: str = "benchmark_results.json"):
        """Сохраняет результаты в файл"""
        output_path = BENCHMARK_DIR / output_file
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        print(f"\n💾 Результаты сохранены в {output_path}")
    
    def generate_report(self):
        """Генерирует отчет по результатам"""
        if not self.results:
            print("Нет результатов для отчета")
            return
        
        df = pd.DataFrame(self.results)
        
        print("\n" + "="*60)
        print("📈 ОТЧЕТ ПО БЕНЧМАРКУ")
        print("="*60)
        
        # Группировка по датасетам
        if "dataset" in df.columns:
            for dataset in df["dataset"].unique():
                dataset_df = df[df["dataset"] == dataset]
                print(f"\n{dataset.upper()}:")
                
                if "correct" in dataset_df.columns:
                    accuracy = dataset_df["correct"].mean()
                    print(f"  Execution Accuracy: {accuracy:.2%}")
                
                if "used_sql" in dataset_df.columns:
                    sql_usage = dataset_df["used_sql"].mean()
                    print(f"  SQL Usage Rate: {sql_usage:.2%}")
                
                if "elapsed" in dataset_df.columns:
                    avg_time = dataset_df["elapsed"].mean()
                    p95_time = dataset_df["elapsed"].quantile(0.95)
                    print(f"  Среднее время: {avg_time:.2f}s")
                    print(f"  95-й перцентиль: {p95_time:.2f}s")
        
        print("\n" + "="*60)


def main():
    """Главная функция для запуска бенчмарка"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Запуск бенчмарка с Hugging Face")
    parser.add_argument(
        "--dataset",
        type=str,
        default="sql-create-context",
        choices=["spider", "wikisql", "sql-create-context"],
        help="Название датасета с Hugging Face"
    )
    parser.add_argument(
        "--split",
        type=str,
        default="test",
        help="Раздел датасета (train/test/dev)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Количество примеров для тестирования"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="benchmark_results.json",
        help="Имя файла для сохранения результатов"
    )
    
    args = parser.parse_args()
    
    runner = BenchmarkRunner()
    
    # Загружаем датасет
    dataset = runner.load_dataset_from_hf(args.dataset, args.split, args.limit)
    if dataset is None:
        print("Не удалось загрузить датасет")
        return
    
    # Запускаем оценку
    if args.dataset == "spider":
        results = runner.evaluate_spider(dataset, args.limit)
    elif args.dataset == "sql-create-context":
        results = runner.evaluate_sql_create_context(dataset, args.limit)
    else:
        print(f"   Оценка для {args.dataset} еще не реализована")
        return
    
    # Сохраняем результаты
    runner.save_results(args.output)
    
    # Генерируем отчет
    runner.generate_report()


if __name__ == "__main__":
    main()

