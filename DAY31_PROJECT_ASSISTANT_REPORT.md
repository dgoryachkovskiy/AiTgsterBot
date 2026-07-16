# Day31. Developer Assistant With RAG And MCP

## Summary

- generated_at: `2026-07-16T08:23:20+00:00`
- project_root: `C:\Users\pospi\Documents\Codex`
- indexed_sources: `README.md, docs\PROJECT_COMMANDS.md, docs\PROJECT_STRUCTURE.md`
- chunks: `22`
- embedding_provider: `local_hashing_v1`
- mcp_tools: `get_git_branch, list_project_files, get_git_diff_stat`
- git_branch_from_mcp: `codex/day_31_project_dev_assistant`
- llm_used: `True`

## /help Result

- question: `Какая структура проекта и какая текущая git-ветка?`

Ответ: Структура проекта включает следующие основные компоненты:

1. **Основные файлы проекта:**
   - `README.md` — основной файл документации
   - `docs/` — директория с дополнительной документацией
   - `day9_deepseek_compare.py` — сравнение сжатия через DeepSeek API
   - `day9_compression_demo.py` — локальный детерминированный помощник
   - `day31_dev_assistant.py` — ассистент разработчика (текущий день)

2. **Локальный LLM слой:**
   - `day26_local_llm.py` — проверка локальных моделей Ollama
   - `day27_local_llm_app.py` — интеграция локального LLM в CLI
   - `day28_local_rag.py` — RAG генерация через локальную модель
   - `day29_local_llm_optimizer.py` — сравнение настроек локальных моделей
   - `day30_private_llm_service.py` — HTTP API и веб-чат для локального LLM

3. **Документация:**
   - `docs/PROJECT_COMMANDS.md` — команды проекта
   - `docs/PROJECT_STRUCTURE.md` — структура проекта

4. **Виртуальное окружение:** `.venv/`

Git: branch=codex/day_31_project_dev_assistant, commit=2761d5c
Источники: README.md section=Day 9 Commands chunk_id=readme_md_004; docs/PROJECT_COMMANDS.md section=Day31 Developer Assistant Commands chunk_id=docs_project_commands_md_007; docs/PROJECT_STRUCTURE.md section=Local LLM Layer chunk_id=docs_project_structure_md_006; README.md section=Other Demo Commands chunk_id=readme_md_008

## Sources

| score | source | section | chunk_id |
|---:|---|---|---|
| 0.1158 | README.md | Day 9 Commands | readme_md_004 |
| 0.0998 | docs\PROJECT_COMMANDS.md | Day31 Developer Assistant Commands | docs_project_commands_md_007 |
| 0.0440 | docs\PROJECT_COMMANDS.md | Local LLM Commands | docs_project_commands_md_006 |
| 0.0390 | docs\PROJECT_STRUCTURE.md | Local LLM Layer | docs_project_structure_md_006 |
| 0.0176 | README.md | Other Demo Commands | readme_md_008 |

## MCP Tools

```json
[
  {
    "name": "get_git_branch",
    "description": "Return current git branch and short commit for the project.",
    "input_schema": {
      "properties": {},
      "title": "get_git_branchArguments",
      "type": "object"
    }
  },
  {
    "name": "list_project_files",
    "description": "Return a read-only list of tracked and untracked project files.",
    "input_schema": {
      "properties": {
        "limit": {
          "default": 120,
          "description": "Maximum number of file paths to return.",
          "maximum": 500,
          "minimum": 1,
          "title": "Limit",
          "type": "integer"
        }
      },
      "title": "list_project_filesArguments",
      "type": "object"
    }
  },
  {
    "name": "get_git_diff_stat",
    "description": "Return current git diff stat and changed tracked files.",
    "input_schema": {
      "properties": {},
      "title": "get_git_diff_statArguments",
      "type": "object"
    }
  }
]
```

## Check Commands

```powershell
.\.venv\Scripts\python.exe day31_dev_assistant.py index
.\.venv\Scripts\python.exe day31_dev_assistant.py mcp-tools
.\.venv\Scripts\python.exe day31_dev_assistant.py /help "Какая структура проекта?"
.\.venv\Scripts\python.exe day31_dev_assistant.py demo
```
