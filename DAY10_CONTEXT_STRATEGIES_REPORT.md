# Day 10 Context Strategies Report

## Telegram Commands

```text
/day10
/day10_api
/strategy sliding
/strategy facts
/strategy branching
/strategy off
/checkpoint base
/branch cheap base
/branch quality base
/switch_branch cheap
/switch_branch quality
/day10_status
```

## Local Demo

```powershell
.venv\Scripts\python.exe day10_context_strategies.py
```

Shows local deterministic comparison of exactly 3 strategies:

1. Sliding Window
2. Sticky Facts / Key-Value Memory
3. Branching

Branching contains two independent branches from one checkpoint, but it is still one strategy.

## DeepSeek API Comparison

```powershell
.venv\Scripts\python.exe day10_deepseek_compare.py --send-telegram --recent-messages 6
```

Output sent to Telegram chat `439057315`:

```text
День 10. Сравнение стратегий через DeepSeek
model=deepseek-v4-flash
recent_messages=6
Сценарий: 12 сообщений собирают ТЗ, финальный вопрос одинаковый.
Стратегий ровно 3: sliding, facts, branching. У branching внутри 2 ветки.

sliding
quality=0/6
context_tokens_est~122
prompt=130, answer=41, total=171
cost~$0.000012, time=1.8s
answer={
  "goal": null,
  "constraint": null,
  "decision": null,
  "stack": null,
  "budget": null,
  "deadline": null
}

facts
quality=6/6
context_tokens_est~235
prompt=213, answer=74, total=287
cost~$0.000033, time=1.3s
answer={
  "goal": "Telegram-агент поддержки",
  "constraint": "ответ до 2 секунд",
  "decision": "DeepSeek API",
  "stack": "Python, python-telegram-bot, JSON",
  "budget": "минимальный",
  "deadline": "пятница"
}

branching (one strategy, two branches from one checkpoint)
  branch=cheap
  quality=5/6
  context_tokens_est~236
  prompt=213, answer=84, total=297
  cost~$0.000036, time=1.5s
  answer={
    "goal": "Telegram-агент поддержки",
    "constraint": "ответ до 2 секунд",
    "decision": "экономим токены и держим краткий контекст",
    "stack": "Python, python-telegram-bot, JSON",
    "budget": "минимальный",
    "deadline": "пятница"
  }

  branch=quality
  quality=5/6
  context_tokens_est~242
  prompt=209, answer=83, total=292
  cost~$0.000035, time=1.5s
  answer={
    "goal": "Telegram-агент поддержки",
    "constraint": "ответ до 2 секунд",
    "decision": "сохраняем максимум требований для стабильности",
    "stack": "Python, python-telegram-bot, JSON",
    "budget": "минимальный",
    "deadline": "пятница"
  }

Вывод DeepSeek:
Sticky Facts оказался лучшей стратегией: он сохранил все 6 ключевых деталей при умеренном расходе токенов.
Sliding Window самый дешевый, но потерял ранний контекст и вернул пустые поля.
Branching работает как одна стратегия с двумя ветками: каждая ветка помнит базовые факты, но меняет branch-specific decision.
```

## Result

- Sliding Window is cheapest but lost all early details.
- Sticky Facts kept all required details with moderate token cost.
- Branching kept shared base facts and diverged into two independent branches from the same checkpoint.
- Day 10 compares exactly 3 strategies. `cheap` and `quality` are branches inside Branching, not separate strategies.
- No summary is used by Day 10 strategies.
