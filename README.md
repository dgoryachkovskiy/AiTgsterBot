# AiTgsterBot

Telegram bot for comparing the same DeepSeek prompt with two API control levels:

- no restrictions
- explicit response format, length limit, and stop sequence

## Configuration

Create `.env` from `.env.example`:

```bash
cp .env.example .env
```

Set the required values:

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

On Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

## Notes

- Runtime mode: Telegram polling.
- Each user message triggers three DeepSeek API calls: uncontrolled answer, controlled answer, and DeepSeek-based comparison.
- The controlled response uses `response_format={"type":"text"}`, `max_tokens`, `temperature`, and `stop` request parameters.
- Requests disable thinking mode with `extra_body={"thinking":{"type":"disabled"}}` to return normal message text.
- Secrets must stay in `.env`.
- `.env`, `.venv`, and Python cache files are ignored by git.
