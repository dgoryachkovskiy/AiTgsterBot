# Memory Layers Agent

## Модель памяти

- `short_term`: краткосрочная память текущего диалога.
- `working`: рабочая память текущей задачи: стадия, цель, ограничения, план, deliverable.
- `long_term`: долговременная память: профиль, предпочтения, решения, знания.

Данные сохраняются явно: для каждого слоя есть отдельный метод записи и отдельный JSON-файл.
Prompt builder получает список слоев и подмешивает только выбранную память, а не весь store.

## Файлы памяти

- `memory_layers_store\short_term.json`
- `memory_layers_store\working.json`
- `memory_layers_store\long_term.json`
- `memory_layers_store\write_log.json`

## Explicit Write Log

- `short_term` -> `messages[]`: краткосрочная память: текущая реплика пользователя
- `short_term` -> `messages[]`: краткосрочная память: ответ ассистента в текущем диалоге
- `short_term` -> `messages[]`: краткосрочная память: актуальный запрос в текущей сессии
- `working` -> `task.stage`: рабочая память: текущий этап задачи
- `working` -> `task.goal`: рабочая память: цель активной задачи
- `working` -> `task.constraints`: рабочая память: ограничения текущей задачи
- `working` -> `task.plan`: рабочая память: план выполнения текущей задачи
- `working` -> `task.deliverable`: рабочая память: ожидаемый результат
- `working` -> `notes[]`: рабочая память: метод проверки влияния слоев
- `long_term` -> `profile.name`: долговременная память: профиль пользователя
- `long_term` -> `preferences.answer_style`: долговременная память: стабильное предпочтение формата ответа
- `long_term` -> `decisions.llm_provider`: долговременная память: решение применимо к будущим заданиям
- `long_term` -> `decisions.memory_policy`: долговременная память: политика сохранения памяти
- `long_term` -> `knowledge.project`: долговременная память: знание о проекте

## Memory Snapshot

```json
{
  "short_term": {
    "layer": "short_term",
    "description": "Краткосрочная память: текущий диалог и последние реплики.",
    "messages": [
      {
        "role": "user",
        "content": "Нужно сделать ассистента с явной моделью памяти.",
        "saved_at": "2026-06-19T08:17:56+00:00"
      },
      {
        "role": "assistant",
        "content": "Разделю память на short_term, working и long_term.",
        "saved_at": "2026-06-19T08:17:56+00:00"
      },
      {
        "role": "user",
        "content": "Проверь через DeepSeek API, как выбранные слои влияют на ответ.",
        "saved_at": "2026-06-19T08:17:56+00:00"
      }
    ]
  },
  "working": {
    "layer": "working",
    "description": "Рабочая память: данные текущей задачи и состояние процесса.",
    "task": {
      "stage": "planning",
      "goal": "описать и реализовать модель памяти для ассистента",
      "constraints": [
        "минимум 3 типа памяти",
        "разные типы памяти хранятся отдельно",
        "выбор слоя для сохранения должен быть явным",
        "использовать реальные вызовы DeepSeek API",
        "не трогать Telegram-бота"
      ],
      "plan": [
        "создать файловое хранилище слоев",
        "реализовать prompt builder с выбором слоев",
        "сравнить ответы DeepSeek при разных наборах памяти",
        "записать markdown-отчет и mp4-демо"
      ],
      "deliverable": "CLI-агент, JSON memory store, DeepSeek API отчет, видео демонстрация"
    },
    "notes": [
      "Один и тот же вопрос будет задан с all_layers, without_working, without_long_term и short_term_only."
    ]
  },
  "long_term": {
    "layer": "long_term",
    "description": "Долговременная память: профиль, предпочтения, решения и знания.",
    "profile": {
      "name": "Данил"
    },
    "preferences": {
      "answer_style": "коротко и по делу, без лишней теории"
    },
    "decisions": {
      "llm_provider": "использовать DeepSeek API для практических проверок",
      "memory_policy": "профиль и стабильные решения хранить в long_term, детали активной задачи в working"
    },
    "knowledge": {
      "project": "AiTgsterBot - Telegram-ассистент, который использует DeepSeek API."
    }
  }
}
```

## Prompt Layer Selection

- `all_layers` selected layers: `short_term, working, long_term`
- `without_working` selected layers: `short_term, long_term`
- `without_long_term` selected layers: `short_term, working`
- `short_term_only` selected layers: `short_term`

## DeepSeek API Check

model=deepseek-v4-flash
question=Кто пользователь, какой стиль ответа он предпочитает, какая текущая задача, какая стадия, ограничения, deliverable, стабильные решения и знание о проекте? Верни JSON с ключами user, preferences, current_task, stage, constraints, deliverable, decisions, project.
total_tokens=2992
total_cost~$0.000503

### all_layers

layers=short_term, working, long_term
prompt=823, answer=228, total=1051
cost~$0.000179, time=2.8s, attempts=1

```json
{
  "user": "Данил",
  "preferences": "коротко и по делу, без лишней теории",
  "current_task": "описать и реализовать модель памяти для ассистента",
  "stage": "planning",
  "constraints": ["минимум 3 типа памяти", "разные типы памяти хранятся отдельно", "выбор слоя для сохранения должен быть явным", "использовать реальные вызовы DeepSeek API", "не трогать Telegram-бота"],
  "deliverable": "CLI-агент, JSON memory store, DeepSeek API отчет, видео демонстрация",
  "decisions": {"llm_provider": "использовать DeepSeek API для практических проверок", "memory_policy": "профиль и стабильные решения хранить в long_term, детали активной задачи в working"},
  "project": "AiTgsterBot - Telegram-ассистент, который использует DeepSeek API."
}
```

### without_working

layers=short_term, long_term
prompt=551, answer=143, total=694
cost~$0.000117, time=1.7s, attempts=1

```json
{
  "user": "Данил",
  "preferences": "коротко и по делу, без лишней теории",
  "current_task": null,
  "stage": null,
  "constraints": null,
  "deliverable": null,
  "decisions": {
    "llm_provider": "использовать DeepSeek API для практических проверок",
    "memory_policy": "профиль и стабильные решения хранить в long_term, детали активной задачи в working"
  },
  "project": "AiTgsterBot - Telegram-ассистент, который использует DeepSeek API."
}
```

### without_long_term

layers=short_term, working
prompt=643, answer=154, total=797
cost~$0.000133, time=2.8s, attempts=1

```json
{
  "user": null,
  "preferences": null,
  "current_task": "описать и реализовать модель памяти для ассистента",
  "stage": "planning",
  "constraints": [
    "минимум 3 типа памяти",
    "разные типы памяти хранятся отдельно",
    "выбор слоя для сохранения должен быть явным",
    "использовать реальные вызовы DeepSeek API",
    "не трогать Telegram-бота"
  ],
  "deliverable": "CLI-агент, JSON memory store, DeepSeek API отчет, видео демонстрация",
  "decisions": null,
  "project": null
}
```

### short_term_only

layers=short_term
prompt=371, answer=79, total=450
cost~$0.000074, time=1.5s, attempts=1

```json
{
  "user": null,
  "preferences": null,
  "current_task": "Проверка влияния выбранных слоёв памяти на ответ через DeepSeek API",
  "stage": "тестирование",
  "constraints": null,
  "deliverable": null,
  "decisions": null,
  "project": null
}
```

## Вывод

- `all_layers`: ассистент видит текущий диалог, задачу, профиль, решения и знания.
- `without_working`: ассистент сохраняет профиль и проект, но теряет цель, стадию, ограничения и deliverable текущей задачи.
- `without_long_term`: ассистент понимает текущую задачу, но теряет профиль пользователя и стабильные решения.
- `short_term_only`: ассистент видит только последние реплики и не знает ни рабочее состояние, ни долговременную память.