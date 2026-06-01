import asyncio
import logging
import os
from typing import Iterable

from dotenv import load_dotenv
from openai import APIConnectionError, APIError, AuthenticationError, OpenAI, RateLimitError
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


TELEGRAM_MESSAGE_LIMIT = 4096
SAFE_MESSAGE_LIMIT = 3900
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"


load_dotenv()

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


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


class DeepSeekClient:
    def __init__(self) -> None:
        self.model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
        self.client = OpenAI(
            api_key=require_env("DEEPSEEK_API_KEY"),
            base_url=DEEPSEEK_BASE_URL,
        )

    def ask(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant. Answer clearly and concisely.",
                },
                {"role": "user", "content": prompt},
            ],
        )

        if not response.choices:
            return ""

        return response.choices[0].message.content or ""


deepseek = DeepSeekClient()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message:
        await update.message.reply_text(
            "Привет! Напишите любой вопрос, а я отправлю его в DeepSeek и верну ответ."
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    prompt = update.message.text.strip()
    if not prompt:
        await update.message.reply_text("Пожалуйста, отправьте непустой текстовый запрос.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        answer = await asyncio.to_thread(deepseek.ask, prompt)
    except AuthenticationError:
        logger.exception("DeepSeek authentication failed")
        await update.message.reply_text("Ошибка авторизации DeepSeek. Проверьте DEEPSEEK_API_KEY.")
        return
    except RateLimitError:
        logger.exception("DeepSeek rate limit exceeded")
        await update.message.reply_text("DeepSeek временно ограничил запросы. Попробуйте позже.")
        return
    except APIConnectionError:
        logger.exception("DeepSeek connection error")
        await update.message.reply_text("Не удалось подключиться к DeepSeek. Проверьте сеть и попробуйте снова.")
        return
    except APIError:
        logger.exception("DeepSeek API error")
        await update.message.reply_text("DeepSeek вернул ошибку API. Попробуйте повторить запрос.")
        return
    except Exception:
        logger.exception("Unexpected bot error")
        await update.message.reply_text("Произошла неожиданная ошибка. Попробуйте еще раз.")
        return

    for part in split_telegram_message(answer, SAFE_MESSAGE_LIMIT):
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
