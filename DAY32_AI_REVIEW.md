# Day32 AI Code Review

- generated_at: `2026-07-16T08:49:45+00:00`
- base_ref: `HEAD~1`
- head_ref: `HEAD`
- branch: `codex/day_32_ai_code_review`
- commit: `1b3e48a`
- changed_files: `6`
- diff_chars: `42463`
- diff_truncated: `False`
- llm_used: `True`
- tokens: `{
  "prompt": 20751,
  "completion": 1599,
  "total": 22350
}`

## Потенциальные баги

### 1. Дублирование ключа `DAY30_STORE_DIR` в `.env.example`
**Файл:** `.env.example` (строка 143)
**Проблема:** Ключ `DAY30_STORE_DIR` объявлен дважды — сначала в блоке Day30 (строка 130), затем повторно в блоке Day31 (строка 143). Второе объявление перезапишет первое при загрузке через `load_dotenv()`, что приведёт к потере корректного значения для Day30.
**Рекомендация:** Удалить дублирующуюся строку `DAY30_STORE_DIR=day30_private_llm_store` из блока Day31.

### 2. `wait_for_mcp` не закрывает spawned-процесс при ошибке
**Файл:** `day31_dev_assistant.py`, функция `command_mcp_branch` (строка ~720)
**Проблема:** Если `wait_for_mcp` выбрасывает исключение (таймаут), spawned-процесс MCP-сервера не останавливается в блоке `finally`, потому что исключение происходит до `try`. Это приводит к зомби-процессам.
**Код:**
```python
spawned = None
if not args.mcp_url and not args.no_spawn_mcp:
    spawned = start_mcp_server(...)
    wait_for_mcp(url, 12)  # исключение здесь — spawned не передан в finally
try:
    branch = asyncio.run(call_mcp_tool(...))
finally:
    stop_mcp_server(spawned)  # spawned может быть None
```
**Рекомендация:** Обернуть `wait_for_mcp` в `try` или перенести его внутрь `try`.

### 3. `command_mcp_tools` не ждёт готовности сервера
**Файл:** `day31_dev_assistant.py`, функция `command_mcp_tools` (строка ~690)
**Проблема:** В отличие от `command_mcp_branch`, здесь нет вызова `wait_for_mcp` после запуска сервера. Если сервер не успеет стартовать, `list_mcp_tools` внутри `wait_for_mcp` упадёт с ошибкой соединения.
**Код:**
```python
if not args.mcp_url and not args.no_spawn_mcp:
    spawned = start_mcp_server(...)
    # отсутствует wait_for_mcp(url, 12)
try:
    tools = wait_for_mcp(url, 12)  # может упасть
```
**Рекомендация:** Добавить `wait_for_mcp(url, 12)` после `start_mcp_server`.

### 4. `command_demo` запускает сервер дважды
**Файл:** `day31_dev_assistant.py`, функция `command_demo` (строка ~740)
**Проблема:** Сначала запускается сервер для получения списка инструментов, затем он останавливается, и снова запускается внутри `run_help`. Это избыточно и замедляет выполнение.
**Рекомендация:** Переиспользовать один запущенный сервер для обоих вызовов.

## Архитектурные проблемы

### 1. Жёсткая привязка к `day21_document_indexer`
**Файл:** `day31_dev_assistant.py`, импорт (строка 18)
**Проблема:** Модуль импортирует функции из `day21_document_indexer`, который является legacy-компонентом. Любые изменения в Day21 (например, переименование `create_embedder` или `hash_embedding_dim_from_env`) сломают Day31. Это нарушает принцип слабой связанности.
**Рекомендация:** Вынести общие утилиты (embedding, векторизация) в отдельный shared-модуль, например `codex_shared/embedding_utils.py`.

### 2. Отсутствие graceful shutdown для spawned-процессов
**Файл:** `day31_dev_assistant.py`, функция `stop_mcp_server` (строка ~470)
**Проблема:** При `KeyboardInterrupt` или `SystemExit` spawned-процесс MCP-сервера не гарантированно завершается. Используется `terminate()` + `wait(5)`, но если процесс завис, он останется висеть.
**Рекомендация:** Использовать `atexit.register(stop_mcp_server, spawned)` или контекстный менеджер для управления жизненным циклом процесса.

### 3. Дублирование логики MCP-клиента
**Файлы:** `day31_dev_assistant.py` (строки 380-420) и `day31_project_mcp_server.py` (строки 100-130)
**Проблема:** Логика вызова MCP-инструментов (list_tools, call_tool) дублируется в обоих файлах. При изменении протокола MCP придётся править оба места.
**Рекомендация:** Вынести MCP-клиент в отдельный модуль, например `day31_mcp_client.py`.

## Рекомендации

1. **Добавить обработку `KeyboardInterrupt`** в `main()` для корректного завершения spawned-процессов.
2. **Убрать дублирование `DAY30_STORE_DIR`** из `.env.example`.
3. **Добавить `wait_for_mcp`** в `command_mcp_tools` после запуска сервера.
4. **Оптимизировать `command_demo`** — не запускать сервер дважды.
5. **Вынести общие утилиты** (embedding, векторизация) в shared-модуль.

## RAG источники

- `.env.example` section=`.env.example` chunk_id=`env_example_001` (score=0.6710) — дублирование `DAY30_STORE_DIR`
- `day31_dev_assistant.py` section=`day31_dev_assistant.py` chunk_id=`day31_dev_assistant_py_006` (score=0.5992) — MCP-клиент, wait_for_mcp, stop_mcp_server
- `day31_dev_assistant.py` section=`day31_dev_assistant.py` chunk_id=`day31_dev_assistant_py_001` (score=0.5856) — импорт из day21_document_indexer

## Diff context

- `.env.example`: добавлены блоки Day29, Day30, Day31; дублирование `DAY30_STORE_DIR` в блоке Day31
- `day31_dev_assistant.py`: новый файл (806 строк) — ассистент разработчика с RAG и MCP
- `day31_project_mcp_server.py`: новый файл (152 строки) — read-only MCP сервер для git-контекста
- `requirements.txt`: добавлены `fastapi`, `uvicorn`, `httpx`

## Machine Context

### Changed Files

- `M` `.env.example`
- `M` `.gitignore`
- `A` `DAY31_PROJECT_ASSISTANT_REPORT.md`
- `A` `day31_dev_assistant.py`
- `A` `day31_project_mcp_server.py`
- `M` `requirements.txt`

### RAG Sources

| score | source | section | chunk_id |
|---:|---|---|---|
| 0.6710 | .env.example | .env.example | env_example_001 |
| 0.5992 | day31_dev_assistant.py | day31_dev_assistant.py | day31_dev_assistant_py_006 |
| 0.5925 | day28_local_rag.py | day28_local_rag.py | day28_local_rag_py_001 |
| 0.5920 | day32_code_review_agent.py | day32_code_review_agent.py | day32_code_review_agent_py_001 |
| 0.5862 | day26_local_llm.py | day26_local_llm.py | day26_local_llm_py_001 |
| 0.5856 | day31_dev_assistant.py | day31_dev_assistant.py | day31_dev_assistant_py_001 |
| 0.5799 | day24_rag_citations_agent.py | day24_rag_citations_agent.py | day24_rag_citations_agent_py_001 |
| 0.5685 | day29_local_llm_optimizer.py | day29_local_llm_optimizer.py | day29_local_llm_optimizer_py_001 |
