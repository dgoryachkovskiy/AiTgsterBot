import asyncio
import logging
import os
from typing import Iterable

from dotenv import load_dotenv
from openai import APIConnectionError, APIError, AuthenticationError, OpenAI, RateLimitError
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


SAFE_MESSAGE_LIMIT = 3900
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"

TEMPERATURES = (0.0, 0.7, 1.2, 2.0)
THINKING_DISABLED = {"thinking": {"type": "disabled"}}


load_dotenv()

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def split_telegram_message(text: str, limit: int = SAFE_MESSAGE_LIMIT) -> Iterable[str]:
    if not text:
        return ["DeepSeek returned an empty response."]

    chunks: list[str] = []
    remaining = text

    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at < limit // 2:
            split_at = limit

        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    if remaining:
        chunks.append(remaining)

    return chunks


def truncate_for_prompt(text: str, limit: int = 2200) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}\n...[truncated]"


class DeepSeekClient:
    def __init__(self) -> None:
        self.model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
        self.client = OpenAI(
            api_key=require_env("DEEPSEEK_API_KEY"),
            base_url=DEEPSEEK_BASE_URL,
        )

    def chat(self, system_prompt: str, user_prompt: str, temperature: float) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            extra_body=THINKING_DISABLED,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        if not response.choices:
            return ""

        message = response.choices[0].message
        content = message.content or getattr(message, "reasoning_content", None) or ""
        return content.strip()

    def answer_with_temperature(self, prompt: str, temperature: float) -> str:
        return self.chat(
            (
                "You are a helpful assistant. Answer in Russian. "
                "Answer the user request directly and clearly."
            ),
            prompt,
            temperature=temperature,
        )

    def compare_temperature_answers(self, prompt: str, answers: dict[float, str]) -> str:
        return self.chat(
            (
                "You compare LLM answers in Russian. "
                "Compare accuracy, creativity, and diversity. "
                "Then explain which tasks fit each temperature setting."
            ),
            (
                "Один и тот же запрос был выполнен через API DeepSeek с разными temperature.\n\n"
                f"Исходный запрос:\n{prompt}\n\n"
                f"temperature = 0:\n{truncate_for_prompt(answers[0.0])}\n\n"
                f"temperature = 0.7:\n{truncate_for_prompt(answers[0.7])}\n\n"
                f"temperature = 1.2:\n{truncate_for_prompt(answers[1.2])}\n\n"
                f"temperature = 2:\n{truncate_for_prompt(answers[2.0])}\n\n"
                "Сравни ответы по точности, креативности и разнообразию. "
                "Сформулируй, для каких задач лучше подходит temperature 0, 0.7, 1.2 и 2. "
                "В конце дай короткий итог."
            ),
            temperature=0.2,
        )

    def run_day4_temperature_experiment(self, prompt: str) -> str:
        answers = {
            temperature: self.answer_with_temperature(prompt, temperature)
            for temperature in TEMPERATURES
        }
        comparison = self.compare_temperature_answers(prompt, answers)

        return (
            "День 4. Температура через API DeepSeek\n\n"
            f"Запрос:\n{prompt}\n\n"
            "1. temperature = 0:\n"
            f"{answers[0.0] or 'DeepSeek returned an empty response.'}\n\n"
            "2. temperature = 0.7:\n"
            f"{answers[0.7] or 'DeepSeek returned an empty response.'}\n\n"
            "3. temperature = 1.2:\n"
            f"{answers[1.2] or 'DeepSeek returned an empty response.'}\n\n"
            "4. temperature = 2:\n"
            f"{answers[2.0] or 'DeepSeek returned an empty response.'}\n\n"
            "Сравнение от DeepSeek:\n"
            f"{comparison or 'DeepSeek returned an empty comparison response.'}"
        )


deepseek = DeepSeekClient()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message:
        await update.message.reply_text(
            "Напишите один запрос. Бот выполнит его через DeepSeek с temperature 0, 0.7, 1.2 и 2, "
            "а затем сравнит точность, креативность и разнообразие ответов."
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    prompt = update.message.text.strip()
    if not prompt:
        await update.message.reply_text("Отправьте непустой запрос.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        answer = await asyncio.to_thread(deepseek.run_day4_temperature_experiment, prompt)
    except AuthenticationError:
        logger.exception("DeepSeek authentication failed")
        await update.message.reply_text("Ошибка авторизации DeepSeek. Проверьте DEEPSEEK_API_KEY.")
        return
    except RateLimitError:
        logger.exception("DeepSeek rate limit exceeded")
        await update.message.reply_text("DeepSeek ограничил запросы. Попробуйте позже.")
        return
    except APIConnectionError:
        logger.exception("DeepSeek connection error")
        await update.message.reply_text("Не удалось подключиться к DeepSeek. Проверьте сеть.")
        return
    except APIError:
        logger.exception("DeepSeek API error")
        await update.message.reply_text("DeepSeek вернул ошибку API. Попробуйте повторить запрос.")
        return
    except Exception:
        logger.exception("Unexpected bot error")
        await update.message.reply_text("Произошла неожиданная ошибка. Попробуйте еще раз.")
        return

    for part in split_telegram_message(answer):
        await update.message.reply_text(part)


def main() -> None:
    telegram_token = require_env("TELEGRAM_BOT_TOKEN")

    application = Application.builder().token(telegram_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot is running in polling mode")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
