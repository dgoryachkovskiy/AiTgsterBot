# Day33. AI Support Assistant

## Summary

- generated_at: `2026-07-17T16:45:34+00:00`
- product: `AiTgsterBot`
- RAG index: `C:\Users\pospi\Documents\Codex\day33_support_index_store\support_rag_index.sqlite`
- indexed_sources: `README.md, docs\PROJECT_COMMANDS.md, docs\PROJECT_STRUCTURE.md, day33_support_knowledge\FAQ.md, day33_support_knowledge\TROUBLESHOOTING.md`
- chunks: `36`
- embedding_provider: `local_hashing_v1`
- MCP tools: `get_user_profile, get_ticket, search_support_tickets, list_open_tickets`

## Architecture

- RAG: README, project docs, and Day33 support FAQ/troubleshooting markdown files.
- MCP: `day33_support_mcp_server.py` exposes JSON CRM users and tickets.
- LLM: DeepSeek receives user question, RAG chunks, and MCP CRM context.
- Output: answer, ticket/user context, diagnostic steps, RAG sources, MCP data.

## Results

### Scenario 1

- question: `Почему бот не отвечает на /start после запуска?`
- user_id: `u1001`
- ticket_id: `t9001`
- llm_used: `True`
- tokens: `{'prompt': 2181, 'completion': 391, 'total': 2572}`

Иван, привет! Разбираемся с твоей проблемой.

**Ответ:** Скорее всего, бот использует старый токен. Ты перевыпустил его в BotFather, но в `.env` файле остался старый.

**Учет тикета/пользователя:** Пользователь u1001 (Ivan Petrov), тикет t9001 (высокий приоритет).

**Что проверить:**
1. **Обнови токен в `.env`:** Открой файл `.env` в корне проекта и замени `TELEGRAM_BOT_TOKEN` на новый токен, который ты получил от BotFather.
2. **Перезапусти бота:** Останови старый процесс (Ctrl+C в консоли) и запусти заново:
   ```powershell
   .\.venv\Scripts\python.exe bot.py
   ```
3. **Проверь, что пишешь правильному боту:** Убедись, что в Telegram ты открыл диалог именно с тем ботом, чей новый токен ты вставил в `.env`.

**Источники RAG:**
- `day33_support_knowledge\TROUBLESHOOTING.md` / `Telegram-бот не отвечает` / `day33_support_knowledge_troubleshooting_md_002`
- `day33_support_knowledge\FAQ.md` / `Что такое AiTgsterBot` / `day33_support_knowledge_faq_md_002`

**MCP данные:** `user_id: u1001`, `ticket_id: t9001`, `ticket_search` (найден тикет t9001 с совпадением по описанию).

| score | source | section | chunk_id |
|---:|---|---|---|
| 0.4672 | day33_support_knowledge\TROUBLESHOOTING.md | Telegram-бот не отвечает | day33_support_knowledge_troubleshooting_md_002 |
| 0.3097 | day33_support_knowledge\FAQ.md | Что такое AiTgsterBot | day33_support_knowledge_faq_md_002 |
| 0.3089 | README.md | Run | readme_md_002 |
| 0.2632 | docs\PROJECT_COMMANDS.md | Base Setup | docs_project_commands_md_002 |
| 0.2621 | docs\PROJECT_COMMANDS.md | Telegram Bot | docs_project_commands_md_003 |
| 0.2434 | day33_support_knowledge\FAQ.md | Что делать, если DeepSeek не отвечает | day33_support_knowledge_faq_md_004 |

CRM context:

```json
{
  "user": {
    "found": true,
    "user_id": "u1001",
    "user": {
      "user_id": "u1001",
      "name": "Ivan Petrov",
      "telegram_chat_id": "1001001",
      "plan": "training-pro",
      "timezone": "Europe/Moscow",
      "registered_at": "2026-06-01T09:30:00Z",
      "preferences": {
        "language": "ru",
        "answer_style": "short_steps",
        "needs_sources": true
      },
      "environment": {
        "os": "Windows 11",
        "python": "3.13",
        "launch": ".venv\\Scripts\\python.exe bot.py"
      }
    },
    "tickets": [
      {
        "ticket_id": "t9001",
        "status": "open",
        "priority": "high",
        "subject": "Бот не отвечает после запуска",
        "last_event_at": "2026-07-15T08:25:00Z"
      }
    ]
  },
  "ticket": {
    "found": true,
    "ticket_id": "t9001",
    "ticket": {
      "ticket_id": "t9001",
      "user_id": "u1001",
      "status": "open",
      "priority": "high",
      "channel": "telegram",
      "subject": "Бот не отвечает после запуска",
      "description": "Пользователь запускает bot.py, но Telegram-бот не отвечает на /start и обычные сообщения.",
      "tags": [
        "telegram",
        "startup",
        "token",
        "bot"
      ],
      "created_at": "2026-07-15T08:10:00Z",
      "last_event_at": "2026-07-15T08:25:00Z",
      "events": [
        {
          "at": "2026-07-15T08:10:00Z",
          "type": "user_message",
          "text": "Запускаю .venv\\Scripts\\python.exe bot.py, ошибок не вижу, но /start молчит."
        },
        {
          "at": "2026-07-15T08:18:00Z",
          "type": "support_note",
          "text": "Проверить TELEGRAM_BOT_TOKEN, что запущен именно актуальный bot.py, и что в Telegram открыт правильный бот."
        },
        {
          "at": "2026-07-15T08:25:00Z",
          "type": "user_message",
          "text": "В .env токен есть, но я недавно перевыпустил токен у BotFather."
        }
      ]
    },
    "user": {
      "user_id": "u1001",
      "name": "Ivan Petrov",
      "telegram_chat_id": "1001001",
      "plan": "training-pro",
      "timezone": "Europe/Moscow",
      "registered_at": "2026-06-01T09:30:00Z",
      "preferences": {
        "language": "ru",
        "answer_style": "short_steps",
        "needs_sources": true
      },
      "environment": {
        "os": "Windows 11",
        "python": "3.13",
        "launch": ".venv\\Scripts\\python.exe bot.py"
      }
    }
  },
  "ticket_search": {
    "query": "Почему бот не отвечает на /start после запуска?",
    "status_filter": "",
    "matches": [
      {
        "score": 7,
        "ticket_id": "t9001",
        "user_id": "u1001",
        "status": "open",
        "priority": "high",
        "subject": "Бот не отвечает после запуска",
        "description": "Пользователь запускает bot.py, но Telegram-бот не отвечает на /start и обычные сообщения.",
        "tags": [
          "telegram",
          "startup",
          "token",
          "bot"
        ]
      },
      {
        "score": 4,
        "ticket_id": "t9003",
        "user_id": "u1003",
        "status": "resolved",
        "priority": "low",
        "subject": "История диалога не восстановилась после перезапуска",
        "description": "Пользователь проверяет сохранение контекста и не видит прошлые сообщения после restart.",
        "tags": [
          "context",
          "history",
          "memory",
          "json"
        ]
      },
      {
        "score": 1,
        "ticket_id": "t9002",
        "user_id": "u1002",
        "status": "open",
        "priority": "medium",
        "subject": "Команда /day10 отвечает слишком быстро и кажется без DeepSeek",
        "description": "Пользователь ожидает сравнение стратегий через DeepSeek, но подозревает локальный ответ.",
        "tags": [
          "day10",
          "deepseek",
          "context_strategies"
        ]
      }
    ]
  }
}
```

### Scenario 2

- question: `Почему /day10 выглядит как быстрый локальный ответ, а не DeepSeek-сравнение?`
- user_id: `u1002`
- ticket_id: `t9002`
- llm_used: `True`
- tokens: `{'prompt': 2589, 'completion': 556, 'total': 3145}`

Ответ:  
Скорее всего, команда `/day10` в вашем боте настроена на локальный быстрый ответ, а не на вызов DeepSeek.  
Проверьте, что в коде обработчика `/day10` действительно вызывается `day10_deepseek_compare.py` (или его аналог), а не локальный helper.  
Также убедитесь, что `DEEPSEEK_API_KEY` задан в `.env` и бот может достучаться до DeepSeek.

Учет тикета/пользователя:  
- Пользователь: Maria Sokolova (u1002), план training-basic, язык ru, стиль diagnostic.  
- Тикет: t9002 (открыт, medium), тема — команда /day10 отвечает без DeepSeek.  
- События: пользователь хочет видеть Sliding Window, Facts, Branching через DeepSeek.

Что проверить:  
1) Запустите вручную CLI:  
   `.\.venv\Scripts\python.exe day10_deepseek_compare.py --send-telegram --recent-messages 6`  
   — если вывод приходит в Telegram и содержит DeepSeek-сравнение, значит проблема в обработчике `/day10`.  
2) Убедитесь, что `DEEPSEEK_API_KEY` есть в `.env` и не содержит опечаток.  
3) Проверьте, что в `bot.py` команда `/day10` вызывает именно `day10_deepseek_compare.py`, а не локальную функцию.

Источники RAG:  
- day33_support_knowledge\TROUBLESHOOTING.md section=Day10 не показывает сравнение стратегий chunk_id=day33_support_knowledge_troubleshooting_md_004  
- day33_support_knowledge\FAQ.md section=Команды Day10 chunk_id=day33_support_knowledge_faq_md_007  
- day33_support_knowledge\FAQ.md section=Что делать, если DeepSeek не отвечает chunk_id=day33_support_knowledge_faq_md_004

MCP данные:  
- tool: get_user_by_id (user_id=u1002)  
- tool: get_ticket_by_id (ticket_id=t9002)  
- tool: search_tickets (query=Почему /day10 выглядит как быстрый локальный ответ, а не DeepSeek-сравнение?)

| score | source | section | chunk_id |
|---:|---|---|---|
| 0.4506 | day33_support_knowledge\TROUBLESHOOTING.md | Day10 не показывает сравнение стратегий | day33_support_knowledge_troubleshooting_md_004 |
| 0.3800 | day33_support_knowledge\FAQ.md | Команды Day10 | day33_support_knowledge_faq_md_007 |
| 0.3118 | day33_support_knowledge\FAQ.md | Что делать, если DeepSeek не отвечает | day33_support_knowledge_faq_md_004 |
| 0.2888 | day33_support_knowledge\TROUBLESHOOTING.md | DeepSeek auth error | day33_support_knowledge_troubleshooting_md_003 |
| 0.2695 | day33_support_knowledge\FAQ.md | Команды Day9 | day33_support_knowledge_faq_md_006 |
| 0.2561 | day33_support_knowledge\TROUBLESHOOTING.md | Telegram-бот не отвечает | day33_support_knowledge_troubleshooting_md_002 |

CRM context:

```json
{
  "user": {
    "found": true,
    "user_id": "u1002",
    "user": {
      "user_id": "u1002",
      "name": "Maria Sokolova",
      "telegram_chat_id": "1001002",
      "plan": "training-basic",
      "timezone": "Europe/Moscow",
      "registered_at": "2026-06-10T12:00:00Z",
      "preferences": {
        "language": "ru",
        "answer_style": "diagnostic",
        "needs_sources": true
      },
      "environment": {
        "os": "Windows 10",
        "python": "3.12",
        "launch": ".venv\\Scripts\\python.exe bot.py"
      }
    },
    "tickets": [
      {
        "ticket_id": "t9002",
        "status": "open",
        "priority": "medium",
        "subject": "Команда /day10 отвечает слишком быстро и кажется без DeepSeek",
        "last_event_at": "2026-07-14T15:00:00Z"
      }
    ]
  },
  "ticket": {
    "found": true,
    "ticket_id": "t9002",
    "ticket": {
      "ticket_id": "t9002",
      "user_id": "u1002",
      "status": "open",
      "priority": "medium",
      "channel": "telegram",
      "subject": "Команда /day10 отвечает слишком быстро и кажется без DeepSeek",
      "description": "Пользователь ожидает сравнение стратегий через DeepSeek, но подозревает локальный ответ.",
      "tags": [
        "day10",
        "deepseek",
        "context_strategies"
      ],
      "created_at": "2026-07-14T14:40:00Z",
      "last_event_at": "2026-07-14T15:00:00Z",
      "events": [
        {
          "at": "2026-07-14T14:40:00Z",
          "type": "user_message",
          "text": "Мне нужно, чтобы Day10 сравнивал стратегии именно через DeepSeek."
        },
        {
          "at": "2026-07-14T14:50:00Z",
          "type": "support_note",
          "text": "Проверить DEEPSEEK_API_KEY, команду /day10 или /day10_api, а также токены в выводе CLI day10_deepseek_compare.py."
        },
        {
          "at": "2026-07-14T15:00:00Z",
          "type": "user_message",
          "text": "В Telegram хочу видеть вывод по Sliding Window, Facts и Branching."
        }
      ]
    },
    "user": {
      "user_id": "u1002",
      "name": "Maria Sokolova",
      "telegram_chat_id": "1001002",
      "plan": "training-basic",
      "timezone": "Europe/Moscow",
      "registered_at": "2026-06-10T12:00:00Z",
      "preferences": {
        "language": "ru",
        "answer_style": "diagnostic",
        "needs_sources": true
      },
      "environment": {
        "os": "Windows 10",
        "python": "3.12",
        "launch": ".venv\\Scripts\\python.exe bot.py"
      }
    }
  },
  "ticket_search": {
    "query": "Почему /day10 выглядит как быстрый локальный ответ, а не DeepSeek-сравнение?",
    "status_filter": "",
    "matches": [
      {
        "score": 2,
        "ticket_id": "t9002",
        "user_id": "u1002",
        "status": "open",
        "priority": "medium",
        "subject": "Команда /day10 отвечает слишком быстро и кажется без DeepSeek",
        "description": "Пользователь ожидает сравнение стратегий через DeepSeek, но подозревает локальный ответ.",
        "tags": [
          "day10",
          "deepseek",
          "context_strategies"
        ]
      },
      {
        "score": 1,
        "ticket_id": "t9001",
        "user_id": "u1001",
        "status": "open",
        "priority": "high",
        "subject": "Бот не отвечает после запуска",
        "description": "Пользователь запускает bot.py, но Telegram-бот не отвечает на /start и обычные сообщения.",
        "tags": [
          "telegram",
          "startup",
          "token",
          "bot"
        ]
      },
      {
        "score": 1,
        "ticket_id": "t9003",
        "user_id": "u1003",
        "status": "resolved",
        "priority": "low",
        "subject": "История диалога не восстановилась после перезапуска",
        "description": "Пользователь проверяет сохранение контекста и не видит прошлые сообщения после restart.",
        "tags": [
          "context",
          "history",
          "memory",
          "json"
        ]
      }
    ]
  }
}
```

### Scenario 3

- question: `Почему после перезапуска не восстановилась история диалога?`
- user_id: `u1003`
- ticket_id: `t9003`
- llm_used: `True`
- tokens: `{'prompt': 2210, 'completion': 265, 'total': 2475}`

Олег, судя по вашему тикету **t9003**, проблема уже решена. Причина: вы запускали бота из **другой рабочей директории**, поэтому история записывалась в другой `chat_history.json`.

**Что проверить сейчас:**
1. Запускайте бота строго из корня проекта (где лежит `.env` и `bot.py`).
2. Убедитесь, что `BOT_HISTORY_FILE` указывает на существующий файл.
3. Проверьте, что файл `chat_history.json` доступен на запись.
4. Не вызывали ли вы команду `/reset` — она очищает историю.

**Учет тикета/пользователя:** u1003 / t9003 (resolved)  
**Источники RAG:**  
- `day33_support_knowledge\FAQ.md` — История диалога (score 0.3552)  
- `day33_support_knowledge\TROUBLESHOOTING.md` — Контекст не сохраняется (score 0.2179)  
**MCP данные:** `get_user`, `get_ticket`, `search_tickets`

| score | source | section | chunk_id |
|---:|---|---|---|
| 0.3552 | day33_support_knowledge\FAQ.md | История диалога | day33_support_knowledge_faq_md_008 |
| 0.2179 | day33_support_knowledge\TROUBLESHOOTING.md | Контекст не сохраняется | day33_support_knowledge_troubleshooting_md_005 |
| 0.1886 | day33_support_knowledge\TROUBLESHOOTING.md | Telegram-бот не отвечает | day33_support_knowledge_troubleshooting_md_002 |
| 0.1186 | day33_support_knowledge\TROUBLESHOOTING.md | DeepSeek auth error | day33_support_knowledge_troubleshooting_md_003 |
| 0.1120 | README.md | Run | readme_md_002 |
| 0.0827 | day33_support_knowledge\FAQ.md | Что делать, если DeepSeek не отвечает | day33_support_knowledge_faq_md_004 |

CRM context:

```json
{
  "user": {
    "found": true,
    "user_id": "u1003",
    "user": {
      "user_id": "u1003",
      "name": "Oleg Morozov",
      "telegram_chat_id": "1001003",
      "plan": "training-pro",
      "timezone": "Europe/Moscow",
      "registered_at": "2026-06-15T18:45:00Z",
      "preferences": {
        "language": "ru",
        "answer_style": "explain_then_command",
        "needs_sources": true
      },
      "environment": {
        "os": "Ubuntu 26.04",
        "python": "3.14",
        "launch": "python3 bot.py"
      }
    },
    "tickets": [
      {
        "ticket_id": "t9003",
        "status": "resolved",
        "priority": "low",
        "subject": "История диалога не восстановилась после перезапуска",
        "last_event_at": "2026-07-12T12:05:00Z"
      }
    ]
  },
  "ticket": {
    "found": true,
    "ticket_id": "t9003",
    "ticket": {
      "ticket_id": "t9003",
      "user_id": "u1003",
      "status": "resolved",
      "priority": "low",
      "channel": "cli",
      "subject": "История диалога не восстановилась после перезапуска",
      "description": "Пользователь проверяет сохранение контекста и не видит прошлые сообщения после restart.",
      "tags": [
        "context",
        "history",
        "memory",
        "json"
      ],
      "created_at": "2026-07-12T11:20:00Z",
      "last_event_at": "2026-07-12T12:05:00Z",
      "events": [
        {
          "at": "2026-07-12T11:20:00Z",
          "type": "user_message",
          "text": "После перезапуска бот не помнит предыдущее сообщение."
        },
        {
          "at": "2026-07-12T11:40:00Z",
          "type": "support_note",
          "text": "Проверить BOT_HISTORY_FILE, права на запись, chat_history.json и reset-команду."
        },
        {
          "at": "2026-07-12T12:05:00Z",
          "type": "resolution",
          "text": "Причина: запуск из другой рабочей директории, история писалась в другой chat_history.json."
        }
      ]
    },
    "user": {
      "user_id": "u1003",
      "name": "Oleg Morozov",
      "telegram_chat_id": "1001003",
      "plan": "training-pro",
      "timezone": "Europe/Moscow",
      "registered_at": "2026-06-15T18:45:00Z",
      "preferences": {
        "language": "ru",
        "answer_style": "explain_then_command",
        "needs_sources": true
      },
      "environment": {
        "os": "Ubuntu 26.04",
        "python": "3.14",
        "launch": "python3 bot.py"
      }
    }
  },
  "ticket_search": {
    "query": "Почему после перезапуска не восстановилась история диалога?",
    "status_filter": "",
    "matches": [
      {
        "score": 6,
        "ticket_id": "t9003",
        "user_id": "u1003",
        "status": "resolved",
        "priority": "low",
        "subject": "История диалога не восстановилась после перезапуска",
        "description": "Пользователь проверяет сохранение контекста и не видит прошлые сообщения после restart.",
        "tags": [
          "context",
          "history",
          "memory",
          "json"
        ]
      },
      {
        "score": 2,
        "ticket_id": "t9001",
        "user_id": "u1001",
        "status": "open",
        "priority": "high",
        "subject": "Бот не отвечает после запуска",
        "description": "Пользователь запускает bot.py, но Telegram-бот не отвечает на /start и обычные сообщения.",
        "tags": [
          "telegram",
          "startup",
          "token",
          "bot"
        ]
      }
    ]
  }
}
```

## Check Commands

```powershell
.\.venv\Scripts\python.exe day33_support_assistant.py index
.\.venv\Scripts\python.exe day33_support_assistant.py mcp-tools
.\.venv\Scripts\python.exe day33_support_assistant.py ask "Почему бот не отвечает на /start?" --user-id u1001 --ticket-id t9001
.\.venv\Scripts\python.exe day33_support_assistant.py demo
```
