import asyncio
import math
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
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
DEFAULT_HISTORY_FILE = "chat_history.json"
DEFAULT_MAX_STORED_MESSAGES = 200
DEFAULT_MODEL_CONTEXT_LIMIT = 1_000_000
DEFAULT_MAX_OUTPUT_TOKENS = 1024
TOKEN_OVERHEAD_PER_MESSAGE = 4

MODEL_PRICING_USD_PER_1M = {
    "deepseek-v4-flash": {
        "input_cache_hit": 0.0028,
        "input_cache_miss": 0.14,
        "output": 0.28,
    },
    "deepseek-v4-pro": {
        "input_cache_hit": 0.003625,
        "input_cache_miss": 0.435,
        "output": 0.87,
    },
}


load_dotenv()

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


@dataclass(frozen=True)
class TokenStats:
    current_request_tokens: int
    history_tokens: int
    prompt_tokens_estimate: int
    response_tokens_estimate: int
    prompt_tokens_actual: int
    response_tokens_actual: int
    total_tokens_actual: int
    context_limit: int
    context_remaining_estimate: int
    cost_usd_estimate: float


@dataclass(frozen=True)
class AgentResponse:
    user_request: str
    answer: str
    model: str
    history_messages: int
    token_stats: TokenStats


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
    answer = response.answer or "DeepSeek returned an empty response."
    stats = response.token_stats
    lines = [
        answer,
        "",
        "Токены:",
        f"- текущий запрос: ~{stats.current_request_tokens}",
        f"- история диалога: ~{stats.history_tokens}",
        f"- prompt всего: {stats.prompt_tokens_actual or '~' + str(stats.prompt_tokens_estimate)}",
        f"- ответ модели: {stats.response_tokens_actual or '~' + str(stats.response_tokens_estimate)}",
        f"- остаток контекста: ~{stats.context_remaining_estimate} из {stats.context_limit}",
        f"- стоимость запроса: ~${stats.cost_usd_estimate:.6f}",
    ]
    return "\n".join(lines)


def parse_positive_int(value: str | None, default: int) -> int:
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        logger.warning("Invalid integer value %r, using %s", value, default)
        return default
    return parsed if parsed > 0 else default


class TokenCounter:
    def count_text(self, text: str) -> int:
        if not text:
            return 0

        ascii_chars = 0
        non_ascii_chars = 0
        for char in text:
            if char.isspace():
                continue
            if ord(char) < 128:
                ascii_chars += 1
            else:
                non_ascii_chars += 1

        # DeepSeek bills exact model tokens via API usage. This local counter is
        # an estimate used before the request and for per-message breakdowns.
        return max(1, math.ceil(ascii_chars * 0.3 + non_ascii_chars * 0.6))

    def count_message(self, message: dict[str, str]) -> int:
        return TOKEN_OVERHEAD_PER_MESSAGE + self.count_text(message.get("role", "")) + self.count_text(
            message.get("content", "")
        )

    def count_messages(self, messages: list[dict[str, str]]) -> int:
        return sum(self.count_message(message) for message in messages)


def estimate_request_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
) -> float:
    pricing = MODEL_PRICING_USD_PER_1M.get(model, MODEL_PRICING_USD_PER_1M[DEFAULT_MODEL])
    if cache_hit_tokens or cache_miss_tokens:
        input_cost = (
            cache_hit_tokens * pricing["input_cache_hit"] + cache_miss_tokens * pricing["input_cache_miss"]
        ) / 1_000_000
    else:
        input_cost = prompt_tokens * pricing["input_cache_miss"] / 1_000_000
    output_cost = completion_tokens * pricing["output"] / 1_000_000
    return input_cost + output_cost


class JsonHistoryStore:
    def __init__(self, path: Path, max_messages: int) -> None:
        self.path = path
        self.max_messages = max_messages
        self._lock = RLock()
        self._histories = self._load()

    def get_history(self, chat_id: int) -> list[dict[str, str]]:
        with self._lock:
            return list(self._histories.get(chat_id, []))

    def append_exchange(self, chat_id: int, user_request: str, answer: str) -> None:
        with self._lock:
            history = self._histories.setdefault(chat_id, [])
            history.extend(
                [
                    {"role": "user", "content": user_request},
                    {"role": "assistant", "content": answer},
                ]
            )
            if len(history) > self.max_messages:
                del history[:-self.max_messages]
            self._save()

    def clear_history(self, chat_id: int) -> None:
        with self._lock:
            self._histories.pop(chat_id, None)
            self._save()

    def _load(self) -> dict[int, list[dict[str, str]]]:
        if not self.path.exists():
            return {}

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("Could not load chat history from %s", self.path)
            return {}

        if not isinstance(raw, dict):
            logger.warning("Chat history file %s has invalid format", self.path)
            return {}

        chats = raw.get("chats", raw)
        if not isinstance(chats, dict):
            logger.warning("Chat history file %s has invalid chats section", self.path)
            return {}

        histories: dict[int, list[dict[str, str]]] = {}
        for raw_chat_id, raw_messages in chats.items():
            try:
                chat_id = int(raw_chat_id)
            except (TypeError, ValueError):
                continue

            messages = self._sanitize_messages(raw_messages)
            if messages:
                histories[chat_id] = messages[-self.max_messages :]

        logger.info("Loaded chat history for %s chats from %s", len(histories), self.path)
        return histories

    def _sanitize_messages(self, raw_messages: object) -> list[dict[str, str]]:
        if not isinstance(raw_messages, list):
            return []

        messages: list[dict[str, str]] = []
        for raw_message in raw_messages:
            if not isinstance(raw_message, dict):
                continue

            role = raw_message.get("role")
            content = raw_message.get("content")
            if role in {"user", "assistant"} and isinstance(content, str):
                messages.append({"role": role, "content": content})

        return messages

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "chats": {
                str(chat_id): messages
                for chat_id, messages in sorted(self._histories.items(), key=lambda item: item[0])
            }
        }
        temp_path = self.path.with_name(f"{self.path.name}.tmp")
        temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, self.path)


class SimpleDeepSeekAgent:
    """
    Отдельная сущность агента.
    Инкапсулирует историю чата, прием запроса, вызов LLM через API,
    извлечение ответа и метрик.
    Telegram-бот только передает запрос агенту и выводит результат.
    """

    def __init__(self, client: OpenAI, model: str, context_limit: int, max_output_tokens: int) -> None:
        self.client = client
        self.model = model
        self.context_limit = context_limit
        self.max_output_tokens = max_output_tokens
        self.token_counter = TokenCounter()
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
        current_request_tokens = self.token_counter.count_text(user_request)
        history_tokens = self.token_counter.count_messages(history)
        prompt_tokens_estimate = self.token_counter.count_messages(messages)

        if prompt_tokens_estimate + self.max_output_tokens > self.context_limit:
            raise ValueError(
                "Контекст переполнен: "
                f"prompt ~{prompt_tokens_estimate} + max_output {self.max_output_tokens} "
                f"> limit {self.context_limit}."
            )

        response = self.client.chat.completions.create(
            model=self.model,
            extra_body=THINKING_DISABLED,
            temperature=0.2,
            max_tokens=self.max_output_tokens,
            messages=messages,
        )

        if response.choices:
            message = response.choices[0].message
            answer = message.content or getattr(message, "reasoning_content", None) or ""
        else:
            answer = ""

        usage = response.usage
        prompt_tokens_actual = usage_value(usage, "prompt_tokens")
        response_tokens_actual = usage_value(usage, "completion_tokens")
        total_tokens_actual = usage_value(usage, "total_tokens")
        cache_hit_tokens = usage_value(usage, "prompt_cache_hit_tokens")
        cache_miss_tokens = usage_value(usage, "prompt_cache_miss_tokens")
        response_tokens_estimate = self.token_counter.count_text(answer)
        charged_prompt_tokens = prompt_tokens_actual or prompt_tokens_estimate
        charged_response_tokens = response_tokens_actual or response_tokens_estimate
        cost_usd_estimate = estimate_request_cost_usd(
            model=self.model,
            prompt_tokens=charged_prompt_tokens,
            completion_tokens=charged_response_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
        )

        return AgentResponse(
            user_request=user_request,
            answer=answer.strip(),
            model=self.model,
            history_messages=len(messages),
            token_stats=TokenStats(
                current_request_tokens=current_request_tokens,
                history_tokens=history_tokens,
                prompt_tokens_estimate=prompt_tokens_estimate,
                response_tokens_estimate=response_tokens_estimate,
                prompt_tokens_actual=prompt_tokens_actual,
                response_tokens_actual=response_tokens_actual,
                total_tokens_actual=total_tokens_actual,
                context_limit=self.context_limit,
                context_remaining_estimate=self.context_limit - prompt_tokens_estimate - response_tokens_estimate,
                cost_usd_estimate=cost_usd_estimate,
            ),
        )


class AgentApp:
    def __init__(self) -> None:
        model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
        client = OpenAI(
            api_key=require_env("DEEPSEEK_API_KEY"),
            base_url=DEEPSEEK_BASE_URL,
        )
        context_limit = parse_positive_int(os.getenv("MODEL_CONTEXT_LIMIT"), DEFAULT_MODEL_CONTEXT_LIMIT)
        max_output_tokens = parse_positive_int(os.getenv("MAX_OUTPUT_TOKENS"), DEFAULT_MAX_OUTPUT_TOKENS)
        self.agent = SimpleDeepSeekAgent(
            client=client,
            model=model,
            context_limit=context_limit,
            max_output_tokens=max_output_tokens,
        )
        self.history_store = JsonHistoryStore(
            path=Path(os.getenv("BOT_HISTORY_FILE", DEFAULT_HISTORY_FILE)),
            max_messages=parse_positive_int(os.getenv("MAX_STORED_MESSAGES"), DEFAULT_MAX_STORED_MESSAGES),
        )
        self._lock = RLock()

    def process_user_request(self, chat_id: int, user_request: str) -> AgentResponse:
        with self._lock:
            history = self.history_store.get_history(chat_id)
            response = self.agent.handle(user_request=user_request, history=history)
            self.history_store.append_exchange(chat_id, user_request, response.answer)
            return response

    def clear_history(self, chat_id: int) -> None:
        with self._lock:
            self.history_store.clear_history(chat_id)


agent_app: AgentApp | None = None


def get_agent_app() -> AgentApp:
    global agent_app
    if agent_app is None:
        agent_app = AgentApp()
    return agent_app


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message:
        await update.message.reply_text(
            "День 7. Сохранение контекста. Это чат с памятью: агент помнит прошлые сообщения "
            "в этом Telegram-чате даже после перезапуска приложения. История хранится в JSON "
            "и передается в DeepSeek messages. Команда /reset очищает историю."
        )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not update.effective_chat or not update.message:
        return

    get_agent_app().clear_history(update.effective_chat.id)
    await update.message.reply_text("История чата очищена.")


async def day8_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not update.message:
        return

    await update.message.reply_text("Запускаю Day 8 demo: short, long, overflow.")
    from day8_demo import build_day8_chat_report

    await reply_long(update, build_day8_chat_report())


async def day8_api_overflow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not update.message:
        return

    await update.message.reply_text(
        "Запускаю 3 реальных DeepSeek API запроса. Третий отправит prompt выше лимита модели."
    )
    from day8_real_api_overflow import run_real_api_overflow_demo

    report = await asyncio.to_thread(run_real_api_overflow_demo)
    await reply_long(update, report)


async def day8_degrade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not update.message:
        return

    await update.message.reply_text(
        "Запускаю реальные DeepSeek API запросы: API не должен падать, проверяем качество на длинном шумном контексте."
    )
    from day8_context_degradation import run_context_degradation_demo

    report = await asyncio.to_thread(run_context_degradation_demo)
    await reply_long(update, report)


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
        response = await asyncio.to_thread(
            get_agent_app().process_user_request,
            update.effective_chat.id,
            user_request,
        )
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
    except ValueError as error:
        logger.exception("Token limit exceeded")
        await update.message.reply_text(str(error))
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
    application.add_handler(CommandHandler("day8", day8_tokens))
    application.add_handler(CommandHandler("tokens", day8_tokens))
    application.add_handler(CommandHandler("day8_api", day8_api_overflow))
    application.add_handler(CommandHandler("day8_degrade", day8_degrade))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot is running in polling mode")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
