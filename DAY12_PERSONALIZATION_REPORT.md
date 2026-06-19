# Day 12. Персонализация ассистента

## Принцип

- Профиль пользователя хранится в `long_term`.
- Стиль, формат и ограничения лежат в `long_term.preferences`.
- Prompt builder автоматически подключает профиль к каждому запросу.
- Один и тот же вопрос отправляется в DeepSeek API для разных профилей.

store_root=`day12_personalization_store`
model=deepseek-v4-flash
question=Объясни, как добавить memory layers в Telegram-бота на DeepSeek API.
total_tokens=2186
total_cost~$0.000417

## Profiles

### beginner

```json
{
  "profile_id": "beginner",
  "name": "Данил",
  "role": "начинающий разработчик Telegram-ботов",
  "experience": "учится строить агентов, знает Python базово",
  "goal": "понять идею и повторить руками",
  "style": "простыми словами, коротко, без сложного жаргона",
  "response_format": "пошаговый список с мини-примером",
  "constraints": [
    "не перегружать терминами",
    "объяснять зачем нужен каждый слой памяти",
    "давать команды, которые можно запустить сразу"
  ],
  "context": "делает учебные задания курса по агентам и DeepSeek API"
}
```
### senior

```json
{
  "profile_id": "senior",
  "name": "Марина",
  "role": "senior backend engineer",
  "experience": "умеет проектировать stateful-сервисы и API",
  "goal": "быстро оценить архитектуру и риски",
  "style": "технически, плотно, без базовых объяснений",
  "response_format": "архитектурные пункты: data model, flow, failure modes, checks",
  "constraints": [
    "указывать границы ответственности",
    "отмечать риски токенов и консистентности",
    "не расписывать очевидные основы Python"
  ],
  "context": "сравнивает варианты production-реализации персонализированного агента"
}
```

## DeepSeek API Results

### beginner

prompt=695, answer=340, total=1035
cost~$0.000193, time=5.7s, attempts=1

Данил, привет! Давай разберёмся с memory layers на пальцах.

**Зачем это нужно:**  
Бот запоминает, кто ты, что любишь и что обсуждали раньше. Без памяти каждый новый вопрос — как разговор с незнакомцем.

**Как добавить (3 шага):**

1. **Создай структуру памяти** — три слоя:
   - `short_term` — последние 5–10 сообщений (текущий диалог)
   - `working` — что делаешь сейчас (задача, этап)
   - `long_term` — профиль пользователя, его предпочтения, важные решения

2. **При каждом запросе к DeepSeek API** собирай промт так:
   ```
   [long_term.profile] + [long_term.preferences] + [short_term] + [новый вопрос пользователя]
   ```

3. **Пример кода (упрощённо):**
   ```python
   prompt = f"Ты ассистент для {profile['name']}. Стиль: {preferences['style']}. История: {short_term}. Вопрос: {user_message}"
   response = deepseek.chat(prompt)
   ```

**Совет:** Начни с двух слоёв — `short_term` (просто список последних сообщений) и `long_term` (имя + цель). Рабочую память добавишь позже, когда бот будет выполнять многошаговые задачи.

### senior

prompt=696, answer=455, total=1151
cost~$0.000225, time=6.8s, attempts=1

Марина, вот архитектурная схема.

**Data model**  
- `short_term`: список последних N сообщений (роль + контент + timestamp). Хранить в Redis с TTL (например, 30 мин).  
- `working`: JSON-объект текущей задачи — `stage`, `goal`, `constraints`, `plan`, `deliverable`. Живёт, пока задача не завершена.  
- `long_term`: три отдельных документа в PostgreSQL/Key-Value store: `profile`, `preferences`, `decisions`. Обновляются через отдельный endpoint.

**Flow**  
1. При входящем сообщении бот загружает `long_term` по user_id.  
2. Собирает `short_term` из Redis (последние 10–20 пар).  
3. Если есть активная `working` — подтягивает её.  
4. Собирает system prompt:  
   - `long_term.profile` + `long_term.preferences` как база.  
   - `long_term.decisions` как правила.  
   - `working` как контекст задачи.  
   - `short_term` как история.  
5. Отправляет в DeepSeek API.  
6. После ответа:  
   - обновляет `short_term` (добавляет пару user/assistant).  
   - если задача завершена — очищает `working`.  
   - если изменились `long_term` — пишет в БД.

**Failure modes**  
- Redis упал → `short_term` пустой, бот работает без истории.  
- DeepSeek timeout → retry 2 раза, иначе fallback-ответ.  
- Консистентность `long_term`: используй CAS (compare-and-swap) или версионирование.

**Checks**  
- Лимит токенов: сумма `short_term` + `long_term` не должна превышать контекст модели.  
- `working` не должна висеть вечно — добавь TTL или watchdog.

## Вывод

- `beginner`: должен получить простой пошаговый ответ с минимумом жаргона.
- `senior`: должен получить плотный архитектурный ответ с рисками и проверками.
- Отличие создается не разными вопросами, а разными профилями в `long_term`.