# AiTgsterBot

Telegram bot for Day 4 assignment: one DeepSeek prompt, three temperature values, one comparison.

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

Send one prompt to the bot.

The bot sends the same prompt to DeepSeek with:

1. `temperature = 0`
2. `temperature = 0.7`
3. `temperature = 1.2`

Then the bot sends all three answers to DeepSeek for comparison:

- accuracy
- creativity
- diversity
- best task types for each temperature setting

## Notes

- Runtime mode: Telegram polling.
- One user message triggers four DeepSeek API calls: three answer calls plus one comparison call.
- Thinking mode is disabled with `extra_body={"thinking":{"type":"disabled"}}` so regular answer text is returned in `message.content`.
- Secrets must stay in `.env`.
- `.env`, `.venv`, and Python cache files are ignored by git.
