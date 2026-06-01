# DeepSeek Telegram Bot

Telegram bot that forwards user text messages to DeepSeek and replies with the LLM response.

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
- Secrets must stay in `.env`.
- `.env`, `.venv`, and Python cache files are ignored by git.
