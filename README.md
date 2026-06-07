# AiTgsterBot

Telegram bot for Day 5 assignment: compare one prompt across weak, medium, and strong DeepSeek model configurations.

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

`DEEPSEEK_MODEL` is kept for compatibility, but Day 5 uses explicit model cases in code.

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

The bot runs the same prompt on three model configurations:

1. Weak: `deepseek-v4-flash`, non-thinking mode.
2. Medium: `deepseek-v4-pro`, non-thinking mode.
3. Strong: `deepseek-v4-pro`, thinking mode.

For each result the bot sends:

- answer text
- elapsed response time
- input/output/total token usage
- cache hit/cache miss token usage
- estimated USD cost

Then the bot asks DeepSeek to compare:

- answer quality
- speed
- resource usage
- cost

## Day 5 Deliverable

Format requested by assignment: Video + Code.

- Code: this repository branch `day5`.
- Video: record Telegram bot run showing one prompt, three model outputs, metrics, final comparison, and source links.

## Sources

- Models & Pricing: https://api-docs.deepseek.com/quick_start/pricing
- Models List: https://api-docs.deepseek.com/api/list-models/
- Token Usage: https://api-docs.deepseek.com/quick_start/token_usage

## Notes

- Runtime mode: Telegram polling.
- One user message triggers four DeepSeek API calls: three model calls plus one comparison call.
- Prices are estimated from official per-1M-token API rates and response usage.
- Secrets must stay in `.env`.
- `.env`, `.venv`, and Python cache files are ignored by git.
