# Бенчмарк для оценки агентской системы

Скрипт для загрузки бенчмарков с Hugging Face и оценки производительности агентской системы с Tool Use.

## Установка зависимостей

```bash
pip install datasets
```

## Доступные бенчмарки

### 1. sql-create-context (рекомендуется)
- **Источник**: `b-mc2/sql-create-context` на Hugging Face
- **Описание**: Датасет с вопросами на естественном языке и соответствующими SQL-запросами
- **Использование**: `python scripts/run_benchmark.py --dataset sql-create-context --limit 20`

### 2. Spider
- **Источник**: `spider` на Hugging Face
- **Описание**: Крупный Text-to-SQL бенчмарк с множеством баз данных
- **Использование**: `python scripts/run_benchmark.py --dataset spider --limit 20`

### 3. WikiSQL
- **Источник**: `wikisql` на Hugging Face
- **Описание**: Простой Text-to-SQL бенчмарк для одной таблицы
- **Использование**: `python scripts/run_benchmark.py --dataset wikisql --limit 20`

## Запуск бенчмарка

### Базовый запуск

```bash
# Убедитесь, что бэкенд запущен
uvicorn backend.main:app --host 127.0.0.1 --port 8000

# В другом терминале запустите бенчмарк
python scripts/run_benchmark.py --dataset sql-create-context --limit 20
```

### Параметры

- `--dataset` - название датасета (по умолчанию: `sql-create-context`)
- `--split` - раздел датасета: `train`, `test`, `dev` (по умолчанию: `test`)
- `--limit` - количество примеров для тестирования (по умолчанию: 20)
- `--output` - имя файла для сохранения результатов (по умолчанию: `benchmark_results.json`)

### Примеры

```bash
# Тестирование на 50 примерах из sql-create-context
python scripts/run_benchmark.py --dataset sql-create-context --limit 50

# Тестирование на Spider с сохранением в другой файл
python scripts/run_benchmark.py --dataset spider --limit 30 --output spider_results.json

# Тестирование на train разделе
python scripts/run_benchmark.py --dataset sql-create-context --split train --limit 20
```

## Метрики

Бенчмарк вычисляет следующие метрики:

1. **Execution Accuracy** - доля запросов, где результат агента совпадает с эталонным результатом
2. **SQL Usage Rate** - доля запросов, где агент использовал инструмент `sql_query`
3. **Среднее время выполнения** - среднее время обработки одного запроса
4. **95-й перцентиль времени** - время выполнения для 95% запросов

## Результаты

Результаты сохраняются в `data/benchmark/benchmark_results.json` и содержат:
- Вопрос пользователя
- Ожидаемый SQL
- SQL, сгенерированный агентом
- Правильность ответа
- Время выполнения

## Интерпретация результатов

- **Execution Accuracy ≥ 0.8** - отличный результат
- **Execution Accuracy ≥ 0.6** - хороший результат
- **Execution Accuracy < 0.6** - требуется улучшение

## Примечания

- Для работы бенчмарка необходимо, чтобы бэкенд был запущен
- Бенчмарк использует реальные запросы к LLM, поэтому может занять время
- Рекомендуется начинать с небольшого `--limit` для тестирования

