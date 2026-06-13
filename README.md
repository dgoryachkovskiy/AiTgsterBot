# AiTgsterBot

Telegram bot with context-management demos.

## Run

```powershell
.venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

## Configuration

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_MODEL=deepseek-v4-flash
BOT_HISTORY_FILE=chat_history.json
DAY10_CONTEXT_FILE=day10_context.json
RECENT_MESSAGES_LIMIT=10
MAX_STORED_MESSAGES=200
MODEL_CONTEXT_LIMIT=1000000
MAX_OUTPUT_TOKENS=1024
```

## Day 9 Commands

Run compression comparison through real DeepSeek API calls:

```text
/day9
```

`/day9` sends the full-history, last-N, and summary+recent contexts to DeepSeek. It also asks DeepSeek to write the final comparison conclusion.

CLI:

```powershell
.venv\Scripts\python.exe day9_deepseek_compare.py --send-telegram
```

Local deterministic helper, without API:

```powershell
.venv\Scripts\python.exe day9_compression_demo.py
```

## Day 10 Commands

Run strategy comparison through real DeepSeek API calls:

```text
/day10
```

Backward-compatible alias:

```text
/day10_api
```

`/day10` and `/day10_api` use DeepSeek twice: first to answer with each strategy, then again to write the final comparison conclusion from those results.

Enable strategy for normal chat messages:

```text
/strategy sliding
/strategy facts
/strategy branching
/strategy off
```

Branching commands:

```text
/checkpoint base
/branch cheap base
/branch quality base
/switch_branch cheap
/switch_branch quality
/day10_status
```

## Strategies

Sliding Window:

- keeps only latest N messages;
- cheapest;
- can lose early requirements.

Sticky Facts / Key-Value Memory:

- extracts facts from user messages;
- sends facts plus latest N messages;
- keeps important details without summary.

Branching:

- saves checkpoints;
- creates independent branches;
- switches active branch;
- useful for comparing alternative requirements.

## Day 10 Demo Result

Local deterministic helper, without API:

```powershell
.venv\Scripts\python.exe day10_context_strategies.py
```

The demo runs the same requirements-gathering scenario through all 3 strategies and compares:

- answer quality;
- stability;
- token cost;
- user convenience.

DeepSeek API comparison:

```powershell
.venv\Scripts\python.exe day10_deepseek_compare.py --send-telegram --recent-messages 6
```

## Other Demo Commands

```text
/day8
/day8_api
/day8_degrade
```
