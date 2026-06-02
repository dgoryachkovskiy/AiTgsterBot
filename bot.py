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
CONTROLLED_MAX_TOKENS = 300
CONTROLLED_TEMPERATURE = 0.2
CONTROLLED_STOP_SEQUENCE = "END_OF_RESPONSE"
COMPARISON_MAX_TOKENS = 350
COMPARISON_TEMPERATURE = 0.2
COMPARISON_RETRY_MAX_TOKENS = 500
THINKING_DISABLED = {"thinking": {"type": "disabled"}}


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


def truncate_for_comparison(text: str, limit: int = 1800) -> str:
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

    def ask_uncontrolled(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            extra_body=THINKING_DISABLED,
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

    def ask_controlled(self, prompt: str) -> str:
        system_prompt = (
            "You are a helpful assistant. Answer in Russian.\n"
            "Use exactly this text format:\n"
            "Краткий ответ: one short sentence.\n"
            "Детали:\n"
            "- bullet 1\n"
            "- bullet 2\n"
            "- bullet 3\n"
            "Итог: one short conclusion.\n"
            "The full answer must be no more than 120 Russian words.\n"
            f"After the conclusion, write {CONTROLLED_STOP_SEQUENCE}."
        )

        response = self.client.chat.completions.create(
            model=self.model,
            extra_body=THINKING_DISABLED,
            max_tokens=CONTROLLED_MAX_TOKENS,
            temperature=CONTROLLED_TEMPERATURE,
            response_format={"type": "text"},
            stop=[CONTROLLED_STOP_SEQUENCE],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        )

        if not response.choices:
            return ""

        content = response.choices[0].message.content or ""
        return content.strip()

    def ask_controlled_with_retry(self, prompt: str) -> str:
        controlled = self.ask_controlled(prompt)
        if controlled:
            return controlled

        logger.warning("Controlled DeepSeek response was empty; retrying once")
        return self.ask_controlled(prompt)

    def compare_responses(self, prompt: str, uncontrolled: str, controlled: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            extra_body=THINKING_DISABLED,
            max_tokens=COMPARISON_MAX_TOKENS,
            temperature=COMPARISON_TEMPERATURE,
            response_format={"type": "text"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You compare two LLM responses in Russian. "
                        "Be concise and practical. "
                        "Use this format: Сравнение, Отличия, Вывод."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Один и тот же запрос был отправлен в DeepSeek двумя способами: "
                        "без ограничений и с параметрами контроля ответа через API.\n\n"
                        f"Исходный запрос:\n{prompt}\n\n"
                        f"Ответ без ограничений:\n{uncontrolled}\n\n"
                        "Ответ с ограничениями "
                        f"(response_format=text, max_tokens={CONTROLLED_MAX_TOKENS}, "
                        f"temperature={CONTROLLED_TEMPERATURE}, stop={CONTROLLED_STOP_SEQUENCE!r}):\n"
                        f"{controlled}\n\n"
                        "Сравни ответы: структура, длина, предсказуемость, полнота. "
                        "В конце сделай вывод о том, как параметры API повлияли на результат."
                    ),
                },
            ],
        )

        if not response.choices:
            return ""

        return (response.choices[0].message.content or "").strip()

    def compare_responses_with_retry(self, prompt: str, uncontrolled: str, controlled: str) -> str:
        comparison = self.compare_responses(prompt, uncontrolled, controlled)
        if comparison:
            return comparison

        logger.warning("Comparison DeepSeek response was empty; retrying with a shorter prompt")
        response = self.client.chat.completions.create(
            model=self.model,
            extra_body=THINKING_DISABLED,
            max_tokens=COMPARISON_RETRY_MAX_TOKENS,
            temperature=COMPARISON_TEMPERATURE,
            response_format={"type": "text"},
            messages=[
                {
                    "role": "system",
                    "content": "Сравни два ответа на русском языке. Верни только текст: Сравнение, Отличия, Вывод.",
                },
                {
                    "role": "user",
                    "content": (
                        f"Запрос: {prompt}\n\n"
                        f"Ответ 1 без ограничений:\n{truncate_for_comparison(uncontrolled)}\n\n"
                        f"Ответ 2 с API-ограничениями:\n{truncate_for_comparison(controlled)}\n\n"
                        "Сделай короткое сравнение структуры, длины и предсказуемости."
                    ),
                },
            ],
        )

        if not response.choices:
            return ""

        return (response.choices[0].message.content or "").strip()

    def compare_control_levels(self, prompt: str) -> str:
        uncontrolled = self.ask_uncontrolled(prompt)
        controlled = self.ask_controlled_with_retry(prompt)
        comparison = self.compare_responses_with_retry(prompt, uncontrolled, controlled)
        if not comparison:
            comparison = "DeepSeek returned an empty response for the comparison request."

        return (
            "Один и тот же запрос с разным уровнем контроля ответа через API DeepSeek\n\n"
            f"Запрос:\n{prompt}\n\n"
            "Без ограничений:\n"
            f"{uncontrolled or 'DeepSeek returned an empty response.'}\n\n"
            "С ограничениями:\n"
            'Параметры API: response_format={"type":"text"}, '
            f"max_tokens={CONTROLLED_MAX_TOKENS}, temperature={CONTROLLED_TEMPERATURE}, "
            f"stop={CONTROLLED_STOP_SEQUENCE!r}\n"
            "Формат: краткий ответ, 3 пункта деталей, итог\n\n"
            f"{controlled or 'DeepSeek returned an empty response.'}\n\n"
            "Сравнение от DeepSeek:\n"
            f"{comparison}"
        )


deepseek = DeepSeekClient()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message:
        await update.message.reply_text(
            "Привет! Напишите один запрос, а я сравню два ответа DeepSeek: без ограничений и с контролем формата, длины и завершения."
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
        answer = await asyncio.to_thread(deepseek.compare_control_levels, prompt)
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
