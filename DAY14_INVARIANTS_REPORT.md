# Day 14. Инварианты и ограничения состояния

## Важно

- Инварианты хранятся отдельно от диалога.
- Python checker ищет конфликт до ответа модели.
- DeepSeek получает invariant_policy и объясняет отказ.
- Конфликтный запрос не должен пройти.
- store: `day14_invariants_store`
- model: `deepseek-v4-flash`, total_tokens=2062, cost~$0.000129

## Store Files

- dialog: `day14_invariants_store\dialog.json`
- invariants: `day14_invariants_store\invariants.json`
- checks: `day14_invariants_store\checks.json`

## Invariants

```json
[
  {
    "invariant_id": "architecture-memory-layers",
    "category": "выбранная архитектура",
    "rule": "Архитектура должна сохранять memory layers: short_term, working, long_term.",
    "forbidden_patterns": [
      "один общий prompt",
      "все в один промпт",
      "без memory layers",
      "убрать слои"
    ],
    "safe_alternative": "Оставить отдельные слои памяти и выбирать, какие слои подмешивать в prompt."
  },
  {
    "invariant_id": "stack-python-telegram-deepseek",
    "category": "ограничения по стеку",
    "rule": "Стек проекта: Python, python-telegram-bot, DeepSeek API, JSON storage для учебного демо.",
    "forbidden_patterns": [
      "node.js",
      "express",
      "nestjs",
      "firebase",
      "auth0",
      "supabase"
    ],
    "safe_alternative": "Реализовать в Python через python-telegram-bot и DeepSeek API."
  },
  {
    "invariant_id": "security-no-plain-codes",
    "category": "бизнес-правило / безопасность",
    "rule": "Нельзя хранить одноразовые коды или токены в открытом виде.",
    "forbidden_patterns": [
      "в открытом виде",
      "plain text",
      "без хеша",
      "не хешировать",
      "храни коды как есть"
    ],
    "safe_alternative": "Хранить только хеш кода, TTL, попытки и время истечения."
  },
  {
    "invariant_id": "process-state-machine",
    "category": "принятое техническое решение",
    "rule": "Task state machine обязателен: planning -> execution -> validation -> done.",
    "forbidden_patterns": [
      "без state machine",
      "пропусти planning",
      "сразу execution",
      "без validation"
    ],
    "safe_alternative": "Соблюдать переходы planning -> execution -> validation -> done."
  }
]
```

## DeepSeek API Results

- `allowed` status=allowed, violations=none, tokens=902, attempts=1
  DeepSeek: { "status": "allowed", "considered_invariants": [ "architecture-memory-layers", "stack-python-telegram-deepseek", "security-no-plain-codes", "process-state-machine" ], "refusal_reason": null, "safe_alternative": "Реализовать авторизацию через одноразовый код в Python Telegram-боте: на этапе planning спроектировать схему хранения (хеш кода, TTL, попытки), на execution — отправить код и сохранить хеш, на validation — проверить ввод пользователя по хешу и TTL, на done — завершить авторизацию. Стек: Python, python-telegram-bot, DeepSeek API, JSON storage." }
- `conflict` status=refused, violations=stack-python-telegram-deepseek, security-no-plain-codes, process-state-machine, tokens=1160, attempts=1
  DeepSeek: { "status": "refused", "considered_invariants": ["stack-python-telegram-deepseek", "security-no-plain-codes", "process-state-machine"], "refusal_reason": "Запрос нарушает три инварианта: стек (Firebase и Node.js запрещены, разрешён Python + python-telegram-bot + DeepSeek API), безопасность (коды нельзя хранить в открытом виде, только хеш) и процесс (обязательна state machine planning -> execution -> validation -> done).", "safe_alternative": "Реализуйте авторизацию в Python через python-telegram-bot и DeepSeek API, храните только хеш кода с TTL и попытками, соблюдайте state machine: planning -> execution -> validation -> done." }

## Result

- Allowed request stays inside invariants.
- Conflict request is refused because it violates stack, security, and state-machine invariants.
- Refusal includes reason and safe alternative.