# Day32 AI Code Review

- generated_at: `2026-07-16T09:04:34+00:00`
- base_ref: `HEAD~1`
- head_ref: `HEAD`
- branch: `codex/day_32_ai_code_review`
- commit: `f467281`
- changed_files: `6`
- diff_chars: `49875`
- diff_truncated: `False`
- llm_used: `True`
- tokens: `{
  "prompt": 23470,
  "completion": 1664,
  "total": 25134
}`

# Ревью кода Day32 AI Code Review

## Потенциальные баги

### 1. `wait_for_mcp` не закрывает spawned-процесс при ошибке
**Файл:** `day32_code_review_agent.py`, функция `run_review` (строка ~620)
**Проблема:** Если `wait_for_mcp` выбрасывает исключение (таймаут), spawned-процесс MCP-сервера не останавливается в блоке `finally`, потому что исключение происходит до `try`. Это приводит к зомби-процессам.
**Код:**
```python
spawned = None
if not args.mcp_url and not args.no_spawn_mcp:
    spawned = start_mcp_server(root, store_dir, DEFAULT_MCP_HOST, DEFAULT_MCP_PORT)
    wait_for_mcp(mcp_url, 12)  # исключение здесь — spawned не передан в finally
try:
    branch = asyncio.run(call_mcp_tool(...))
finally:
    stop_mcp_server(spawned)  # spawned может быть None
```
**Рекомендация:** Обернуть `wait_for_mcp` в `try` или перенести его внутрь `try`.

### 2. `command_mcp_tools` не ждёт готовности сервера
**Файл:** `day32_code_review_agent.py`, функция `command_mcp_tools` (строка ~690)
**Проблема:** В отличие от `run_review`, здесь нет вызова `wait_for_mcp` после запуска сервера. Если сервер не успеет стартовать, `wait_for_mcp` внутри `try` упадёт с ошибкой соединения.
**Код:**
```python
if not args.mcp_url and not args.no_spawn_mcp:
    spawned = start_mcp_server(root, store_dir, DEFAULT_MCP_HOST, DEFAULT_MCP_PORT)
    # отсутствует wait_for_mcp(url, 12)
try:
    tools = wait_for_mcp(url, 12)  # может упасть
```
**Рекомендация:** Добавить `wait_for_mcp(url, 12)` после `start_mcp_server`.

### 3. `command_demo` запускает сервер дважды
**Файл:** `day32_code_review_agent.py`, функция `command_demo` (строка ~740)
**Проблема:** Сначала запускается сервер для получения списка инструментов, затем он останавливается, и снова запускается внутри `run_review`. Это избыточно и замедляет выполнение.
**Рекомендация:** Переиспользовать один запущенный сервер для обоих вызовов.

### 4. Дублирование ключа `DAY30_STORE_DIR` в `.env.example`
**Файл:** `.env.example` (строка 143)
**Проблема:** Ключ `DAY30_STORE_DIR` объявлен дважды — сначала в блоке Day30 (строка 130), затем повторно в блоке Day31 (строка 143). Второе объявление перезапишет первое при загрузке через `load_dotenv()`, что приведёт к потере корректного значения для Day30.
**Рекомендация:** Удалить дублирующуюся строку `DAY30_STORE_DIR=day30_private_llm_store` из блока Day31.

## Архитектурные проблемы

### 1. Жёсткая привязка к `day21_document_indexer`
**Файл:** `day32_code_review_agent.py`, импорт (строка 18)
**Проблема:** Модуль импортирует функции из `day21_document_indexer`, который является legacy-компонентом. Любые изменения в Day21 (например, переименование `create_embedder` или `hash_embedding_dim_from_env`) сломают Day32. Это нарушает принцип слабой связанности.
**Рекомендация:** Вынести общие утилиты (embedding, векторизация) в отдельный shared-модуль, например `codex_shared/embedding_utils.py`.

### 2. Отсутствие graceful shutdown для spawned-процессов
**Файл:** `day32_code_review_agent.py`, функция `stop_mcp_server` (строка ~470)
**Проблема:** При `KeyboardInterrupt` или `SystemExit` spawned-процесс MCP-сервера не гарантированно завершается. Используется `terminate()` + `wait(5)`, но если процесс завис, он останется висеть.
**Рекомендация:** Использовать `atexit.register(stop_mcp_server, spawned)` или контекстный менеджер для управления жизненным циклом процесса.

### 3. Дублирование логики MCP-клиента
**Файлы:** `day32_code_review_agent.py` (строки 380-420) и `day32_review_mcp_server.py` (строки 100-130)
**Проблема:** Логика вызова MCP-инструментов (list_tools, call_tool) дублируется в обоих файлах. При изменении протокола MCP придётся править оба места.
**Рекомендация:** Вынести MCP-клиент в отдельный модуль, например `day32_mcp_client.py`.

## Рекомендации

1. **Добавить обработку `KeyboardInterrupt`** в `main()` для корректного завершения spawned-процессов.
2. **Убрать дублирование `DAY30_STORE_DIR`** из `.env.example`.
3. **Добавить `wait_for_mcp`** в `command_mcp_tools` после запуска сервера.
4. **Оптимизировать `command_demo`** — не запускать сервер дважды.
5. **Вынести общие утилиты** (embedding, векторизация) в shared-модуль.

## RAG источники

- `.env.example` section=`.env.example` chunk_id=`env_example_001` (score=0.6787) — дублирование `DAY30_STORE_DIR`
- `day32_code_review_agent.py` section=`day32_code_review_agent.py` chunk_id=`day32_code_review_agent_py_009` (score=0.3372) — MCP-клиент, wait_for_mcp, stop_mcp_server
- `day32_code_review_agent.py` section=`day32_code_review_agent.py` chunk_id=`day32_code_review_agent_py_010` (score=0.3365) — импорт из day21_document_indexer

## Diff context

- `.env.example`: добавлены блоки Day29, Day30, Day31; дублирование `DAY30_STORE_DIR` в блоке Day31
- `day32_code_review_agent.py`: новый файл (801 строка) — ассистент code review с RAG и MCP
- `day32_review_mcp_server.py`: новый файл (200 строк) — read-only MCP сервер для git-контекста
- `.github/workflows/day32-ai-review.yml`: новый файл (93 строки) — GitHub Actions workflow

## Machine Context

### Changed Files

- `M` `.env.example`
- `A` `.github/workflows/day32-ai-review.yml`
- `M` `.gitignore`
- `A` `DAY32_AI_REVIEW.md`
- `A` `day32_code_review_agent.py`
- `A` `day32_review_mcp_server.py`

### RAG Sources

| score | source | section | chunk_id |
|---:|---|---|---|
| 0.6787 | .env.example | .env.example | env_example_001 |
| 0.6554 | .github\workflows\day32-ai-review.yml | day32-ai-review.yml | github_workflows_day32_ai_review_yml_001 |
| 0.3786 | day31_dev_assistant.py | day31_dev_assistant.py | day31_dev_assistant_py_009 |
| 0.3561 | day25_rag_memory_chat.py | day25_rag_memory_chat.py | day25_rag_memory_chat_py_001 |
| 0.3508 | day24_rag_citations_agent.py | day24_rag_citations_agent.py | day24_rag_citations_agent_py_001 |
| 0.3403 | day31_dev_assistant.py | day31_dev_assistant.py | day31_dev_assistant_py_006 |
| 0.3372 | day32_code_review_agent.py | day32_code_review_agent.py | day32_code_review_agent_py_009 |
| 0.3365 | day32_code_review_agent.py | day32_code_review_agent.py | day32_code_review_agent_py_010 |
