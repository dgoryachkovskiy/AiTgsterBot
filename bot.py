import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Iterable

from dotenv import load_dotenv
from openai import APIConnectionError, APIError, AuthenticationError, OpenAI, RateLimitError
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


SAFE_MESSAGE_LIMIT = 3900
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
THINKING_DISABLED = {"thinking": {"type": "disabled"}}
MAX_HISTORY_MESSAGES = 20


load_dotenv()

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


@dataclass(frozen=True)
class AgentResponse:
    user_request: str
    answer: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    prompt_cache_hit_tokens: int
    prompt_cache_miss_tokens: int
    history_messages: int


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


def usage_value(usage: object, name: str) -> int:
    return int(getattr(usage, name, 0) or 0)


def format_agent_response(response: AgentResponse) -> str:
    return (
        "День 6. Первый агент\n\n"
        f"Запрос пользователя:\n{response.user_request}\n\n"
        f"Агент: SimpleDeepSeekAgent\n"
        f"Модель: {response.model}\n"
        f"Сообщений в истории: {response.history_messages}\n"
        f"Токены: input={response.prompt_tokens}, output={response.completion_tokens}, total={response.total_tokens}\n\n"
        f"DeepSeek context cache: hit={response.prompt_cache_hit_tokens}, miss={response.prompt_cache_miss_tokens}\n\n"
        f"Ответ агента:\n{response.answer or 'DeepSeek returned an empty response.'}"
    )


class SimpleDeepSeekAgent:
    """
    Отдельная сущность агента.
    Инкапсулирует историю чата, прием запроса, вызов LLM через API, извлечение ответа и метрик.
    Telegram-бот только передает запрос агенту и выводит результат.
    """

    def __init__(self, client: OpenAI, model: str) -> None:
        self.client = client
        self.model = model
        self.system_prompt = (
            "You are SimpleDeepSeekAgent. Answer in Russian. "
            "Be clear, useful, and concise. If the user asks for code, include code. "
            "Use previous chat messages as context."
        )

    def handle(self, user_request: str, history: list[dict[str, str]]) -> AgentResponse:
        messages = [
            {"role": "system", "content": self.system_prompt},
            *history[-MAX_HISTORY_MESSAGES:],
            {"role": "user", "content": user_request},
        ]

        response = self.client.chat.completions.create(
            model=self.model,
            extra_body=THINKING_DISABLED,
            temperature=0.2,
            messages=messages,
        )

        if response.choices:
            message = response.choices[0].message
            answer = message.content or getattr(message, "reasoning_content", None) or ""
        else:
            answer = ""

        usage = response.usage
        return AgentResponse(
            user_request=user_request,
            answer=answer.strip(),
            model=self.model,
            prompt_tokens=usage_value(usage, "prompt_tokens"),
            completion_tokens=usage_value(usage, "completion_tokens"),
            total_tokens=usage_value(usage, "total_tokens"),
            prompt_cache_hit_tokens=usage_value(usage, "prompt_cache_hit_tokens"),
            prompt_cache_miss_tokens=usage_value(usage, "prompt_cache_miss_tokens"),
            history_messages=len(history) + 2,
        )


class AgentApp:
    def __init__(self) -> None:
        model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
        client = OpenAI(
            api_key=require_env("DEEPSEEK_API_KEY"),
            base_url=DEEPSEEK_BASE_URL,
        )
        self.agent = SimpleDeepSeekAgent(client=client, model=model)
        self.chat_histories: dict[int, list[dict[str, str]]] = {}

    def process_user_request(self, chat_id: int, user_request: str) -> AgentResponse:
        history = self.chat_histories.setdefault(chat_id, [])
        response = self.agent.handle(user_request=user_request, history=history)
        history.extend(
            [
                {"role": "user", "content": user_request},
                {"role": "assistant", "content": response.answer},
            ]
        )
        if len(history) > MAX_HISTORY_MESSAGES:
            del history[:-MAX_HISTORY_MESSAGES]
        return response

    def clear_history(self, chat_id: int) -> None:
        self.chat_histories.pop(chat_id, None)


agent_app = AgentApp()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message:
        await update.message.reply_text(
            "День 6. Первый агент. Это чат с памятью: агент помнит прошлые сообщения в этом Telegram-чате. "
            "История каждый раз передается в DeepSeek messages. Команда /reset очищает историю."
        )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not update.effective_chat or not update.message:
        return

    agent_app.clear_history(update.effective_chat.id)
    await update.message.reply_text("История чата очищена.")


async def reply_long(update: Update, text: str) -> None:
    if not update.message:
        return

    for part in split_telegram_message(text):
        await update.message.reply_text(part)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    user_request = update.message.text.strip()
    if not user_request:
        await update.message.reply_text("Отправьте непустой запрос.")
        return

    if not update.effective_chat:
        await update.message.reply_text("Не удалось определить chat_id.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        response = await asyncio.to_thread(agent_app.process_user_request, update.effective_chat.id, user_request)
        await reply_long(update, format_agent_response(response))
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


def main() -> None:
    telegram_token = require_env("TELEGRAM_BOT_TOKEN")

    application = Application.builder().token(telegram_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot is running in polling mode")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
