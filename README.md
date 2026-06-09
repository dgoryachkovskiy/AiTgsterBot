# AiTgsterBot

Telegram bot for Day 6 assignment: first simple LLM chat agent with memory.

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
```

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

The agent:

1. accepts the user request;
2. loads previous messages for the current Telegram chat;
3. builds the LLM request with chat history;
4. calls DeepSeek through the API;
5. extracts answer text and token usage;
6. saves user and assistant messages back to memory;
7. returns an `AgentResponse`.

The Telegram interface only displays the agent result.

## Agent Boundary

Agent logic is encapsulated in `SimpleDeepSeekAgent`.

Chat memory is stored in `AgentApp.chat_histories` by Telegram `chat_id`.

Telegram handlers do not call DeepSeek directly. They call:

```python
agent_app.process_user_request(chat_id, user_request)
```

Use `/reset` to clear history for the current Telegram chat.

## Result

The agent accepts chat requests, remembers previous questions in the same Telegram chat, and correctly calls LLM through DeepSeek API.

## Notes

- Runtime mode: Telegram polling.
- Default model: `deepseek-v4-flash`.
- Memory window: last 20 role messages per Telegram chat.
- Thinking mode disabled for stable text in `message.content`.
- Secrets must stay in `.env`.
- `.env`, `.venv`, and Python cache files are ignored by git.
