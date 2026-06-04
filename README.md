# AiTgsterBot

Telegram bot for Day 3 assignment: one task, four DeepSeek reasoning strategies, one comparison.

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

Send one logical, algorithmic, or analytical task to the bot.

The bot sends the same task to DeepSeek in four ways:

1. Direct answer without extra instructions.
2. Prompt with instruction: `решай пошагово`.
3. Prompt-generation flow: DeepSeek first writes a better solving prompt, then solves using it.
4. Expert group prompt: analyst, engineer, critic.

Then the bot sends the four results to DeepSeek again for comparison:

- whether answers differ
- which method is more accurate
- where risks or weak points are visible

## Notes

- Runtime mode: Telegram polling.
- One user message triggers six DeepSeek API calls: direct, step-by-step, prompt draft, prompt-based solution, expert group, comparison.
- Secrets must stay in `.env`.
- `.env`, `.venv`, and Python cache files are ignored by git.
