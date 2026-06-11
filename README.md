# AiTgsterBot

Telegram bot for Day 8 assignment: LLM chat agent with persisted context and token accounting.

## Configuration

Create `.env` from `.env.example`:

```bash
cp .env.example .env
```

Set values:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_MODEL=deepseek-v4-flash
BOT_HISTORY_FILE=chat_history.json
MAX_STORED_MESSAGES=200
MODEL_CONTEXT_LIMIT=1000000
MAX_OUTPUT_TOKENS=1024
```

`BOT_HISTORY_FILE` points to the JSON file used for persistent chat memory.
`MAX_STORED_MESSAGES` limits stored role messages per Telegram chat.
`MODEL_CONTEXT_LIMIT` is the model context limit used before sending a request.
`MAX_OUTPUT_TOKENS` reserves output space so the prompt cannot consume the full context.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

## Behavior

Send messages to the Telegram bot as a chat.

The bot passes the request to `SimpleDeepSeekAgent`.

The app:

1. loads previous messages for the current Telegram chat from JSON on startup;
2. accepts the user request;
3. counts tokens for the current request, full stored history, prompt, and reserved output;
4. stops early if prompt plus reserved output exceeds the configured context limit;
5. builds the LLM request with recent chat history;
6. calls DeepSeek through the API;
7. extracts answer text and real API token usage;
8. estimates request cost from input, output, and cache tokens;
9. saves user and assistant messages back to JSON;
10. returns an `AgentResponse`.

The Telegram interface displays the agent answer plus a token report:

```text
Токены:
- текущий запрос: ~12
- история диалога: ~144
- prompt всего: 179
- ответ модели: 52
- остаток контекста: ~999769 из 1000000
- стоимость запроса: ~$0.000040
```

## Persistence Check

1. Start the bot with `python bot.py`.
2. Send a message in Telegram, for example: `Меня зовут Анна`.
3. Stop the bot process.
4. Start the bot again with `python bot.py`.
5. Send: `Как меня зовут?`
6. The agent should answer using the restored history.

The JSON file has this shape:

```json
{
  "chats": {
    "123456789": [
      {"role": "user", "content": "Меня зовут Анна"},
      {"role": "assistant", "content": "Запомнил, вас зовут Анна."}
    ]
  }
}
```

Use `/reset` to clear persisted history for the current Telegram chat.

## Token Experiment

Run the built-in comparison without Telegram:

```powershell
.venv\Scripts\python.exe token_experiment.py
```

Or show the same kind of report directly in Telegram chat:

```text
/day8
```

Alias:

```text
/tokens
```

Run the real DeepSeek API overflow check directly from Telegram:

```text
/day8_api
```

Run the non-overflow quality check:

```text
/day8_degrade
```

This command keeps the API under the limit and checks whether a long noisy context makes retrieval worse. It is probabilistic: a strong model can still find the fact.

This command performs three real API calls. The third call sends a prompt above the model context limit and should return an API error similar to:

```text
This model's maximum context length is 1048565 tokens.
However, you requested 1080073 tokens.
```

The real run used a second request with `1,000,055` prompt tokens, then the third request added more context and failed because the accumulated request exceeded the API limit.

The script runs three scenarios:

1. short dialog;
2. long dialog;
3. simulated overflow dialog with a small context limit.

The overflow scenario intentionally lowers demo `context_limit` to avoid spending money on a real 1M-token request. It still uses the real bot guard: the agent refuses the request before calling the model when:

```text
prompt_tokens + MAX_OUTPUT_TOKENS > MODEL_CONTEXT_LIMIT
```

## Agent Boundary

Agent logic is encapsulated in `SimpleDeepSeekAgent`.

Persistent memory is stored by `JsonHistoryStore` and used by `AgentApp`.
Histories are keyed by Telegram `chat_id`.

DeepSeek `/chat/completions` is stateless, so the server does not keep conversation state. The agent sends previous chat messages inside `messages` on each request, matching the official multi-round conversation guide.

DeepSeek context caching can reduce repeated-prefix cost/latency, but it is cache, not persistent memory.

Telegram handlers do not call DeepSeek directly. They call:

```python
agent_app.process_user_request(chat_id, user_request)
```

## Result

The agent accepts chat requests, saves messages to JSON, restores them after restart, counts tokens, estimates cost growth, and shows how context overflow changes behavior.

## Sources

- Multi-round Conversation: https://api-docs.deepseek.com/guides/multi_round_chat
- Context Caching: https://api-docs.deepseek.com/guides/kv_cache

## Notes

- Runtime mode: Telegram polling.
- Default model: `deepseek-v4-flash`.
- LLM context window: last 20 role messages per Telegram chat.
- Stored memory: last 200 role messages per Telegram chat by default.
- Default context limit: 1,000,000 tokens.
- Default output reservation: 1,024 tokens.
- Thinking mode disabled for stable text in `message.content`.
- Secrets must stay in `.env`.
- `.env`, `.venv`, Python cache files, `chat_history.json`, and experiment output files are ignored by git.
