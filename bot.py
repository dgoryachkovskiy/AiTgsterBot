import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Iterable

from dotenv import load_dotenv
from openai import APIConnectionError, APIError, AuthenticationError, OpenAI, RateLimitError
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


SAFE_MESSAGE_LIMIT = 3900
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
THINKING_DISABLED = {"thinking": {"type": "disabled"}}
THINKING_ENABLED = {"thinking": {"type": "enabled"}}

SOURCE_LINKS = (
    "Источники:\n"
    "- Models & Pricing: https://api-docs.deepseek.com/quick_start/pricing\n"
    "- Models List: https://api-docs.deepseek.com/api/list-models/\n"
    "- Token Usage: https://api-docs.deepseek.com/quick_start/token_usage"
)


load_dotenv()

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


@dataclass(frozen=True)
class ModelCase:
    label: str
    model: str
    thinking: bool
    input_cache_hit_price_per_m: float
    input_cache_miss_price_per_m: float
    output_price_per_m: float


@dataclass(frozen=True)
class ModelRunResult:
    case: ModelCase
    answer: str
    elapsed_seconds: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    prompt_cache_hit_tokens: int
    prompt_cache_miss_tokens: int
    estimated_cost_usd: float


MODEL_CASES = (
    ModelCase(
        label="Слабая модель: DeepSeek V4 Flash, non-thinking",
        model="deepseek-v4-flash",
        thinking=False,
        input_cache_hit_price_per_m=0.0028,
        input_cache_miss_price_per_m=0.14,
        output_price_per_m=0.28,
    ),
    ModelCase(
        label="Средняя модель: DeepSeek V4 Pro, non-thinking",
        model="deepseek-v4-pro",
        thinking=False,
        input_cache_hit_price_per_m=0.003625,
        input_cache_miss_price_per_m=0.435,
        output_price_per_m=0.87,
    ),
    ModelCase(
        label="Сильная модель: DeepSeek V4 Pro, thinking",
        model="deepseek-v4-pro",
        thinking=True,
        input_cache_hit_price_per_m=0.003625,
        input_cache_miss_price_per_m=0.435,
        output_price_per_m=0.87,
    ),
)


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


def usage_value(usage: object, name: str) -> int:
    return int(getattr(usage, name, 0) or 0)


def calculate_cost_usd(case: ModelCase, usage: object) -> float:
    prompt_tokens = usage_value(usage, "prompt_tokens")
    completion_tokens = usage_value(usage, "completion_tokens")
    cache_hit = usage_value(usage, "prompt_cache_hit_tokens")
    cache_miss = usage_value(usage, "prompt_cache_miss_tokens")

    if cache_hit == 0 and cache_miss == 0:
        cache_miss = prompt_tokens

    return (
        cache_hit * case.input_cache_hit_price_per_m
        + cache_miss * case.input_cache_miss_price_per_m
        + completion_tokens * case.output_price_per_m
    ) / 1_000_000


def format_cost(cost: float) -> str:
    return f"${cost:.8f}"


def format_model_result(result: ModelRunResult) -> str:
    return (
        f"{result.case.label}\n"
        f"model={result.case.model}, thinking={'on' if result.case.thinking else 'off'}\n"
        f"Время ответа: {result.elapsed_seconds:.2f} сек\n"
        f"Токены: input={result.prompt_tokens}, output={result.completion_tokens}, total={result.total_tokens}\n"
        f"Cache: hit={result.prompt_cache_hit_tokens}, miss={result.prompt_cache_miss_tokens}\n"
        f"Оценка стоимости: {format_cost(result.estimated_cost_usd)}\n\n"
        f"Ответ:\n{result.answer or 'DeepSeek returned an empty response.'}"
    )


class DeepSeekClient:
    def __init__(self) -> None:
        self.client = OpenAI(
            api_key=require_env("DEEPSEEK_API_KEY"),
            base_url=DEEPSEEK_BASE_URL,
        )

    def run_model_case(self, prompt: str, case: ModelCase) -> ModelRunResult:
        start_time = time.perf_counter()
        response = self.client.chat.completions.create(
            model=case.model,
            extra_body=THINKING_ENABLED if case.thinking else THINKING_DISABLED,
            temperature=0.2,
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant. Answer in Russian. Be accurate and clear.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        elapsed_seconds = time.perf_counter() - start_time

        if response.choices:
            message = response.choices[0].message
            answer = message.content or getattr(message, "reasoning_content", None) or ""
        else:
            answer = ""

        usage = response.usage
        prompt_tokens = usage_value(usage, "prompt_tokens")
        completion_tokens = usage_value(usage, "completion_tokens")
        total_tokens = usage_value(usage, "total_tokens")
        prompt_cache_hit_tokens = usage_value(usage, "prompt_cache_hit_tokens")
        prompt_cache_miss_tokens = usage_value(usage, "prompt_cache_miss_tokens")

        return ModelRunResult(
            case=case,
            answer=answer.strip(),
            elapsed_seconds=elapsed_seconds,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            prompt_cache_hit_tokens=prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=prompt_cache_miss_tokens,
            estimated_cost_usd=calculate_cost_usd(case, usage),
        )

    def compare_model_results(self, prompt: str, results: list[ModelRunResult]) -> str:
        result_text = "\n\n".join(
            (
                f"{result.case.label}\n"
                f"time={result.elapsed_seconds:.2f}s, "
                f"tokens={result.total_tokens}, cost={format_cost(result.estimated_cost_usd)}\n"
                f"answer:\n{truncate_for_prompt(result.answer)}"
            )
            for result in results
        )

        response = self.client.chat.completions.create(
            model="deepseek-v4-pro",
            extra_body=THINKING_DISABLED,
            temperature=0.2,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You compare model outputs in Russian. Compare quality, speed, and resource usage. "
                        "Give a short practical conclusion."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Один и тот же запрос был выполнен на слабой, средней и сильной модели.\n\n"
                        f"Запрос:\n{prompt}\n\n"
                        f"{result_text}\n\n"
                        "Сравни качество ответов, скорость, ресурсоемкость и стоимость. "
                        "В конце дай короткий вывод о различиях между моделями."
                    ),
                },
            ],
        )

        if not response.choices:
            return ""

        message = response.choices[0].message
        content = message.content or getattr(message, "reasoning_content", None) or ""
        return content.strip()


deepseek = DeepSeekClient()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message:
        await update.message.reply_text(
            "Напишите один запрос. Бот выполнит его на слабой, средней и сильной модели DeepSeek, "
            "замерит время, токены, стоимость и сравнит качество."
        )


async def reply_long(update: Update, text: str) -> None:
    if not update.message:
        return

    for part in split_telegram_message(text):
        await update.message.reply_text(part)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    prompt = update.message.text.strip()
    if not prompt:
        await update.message.reply_text("Отправьте непустой запрос.")
        return

    try:
        await reply_long(update, f"День 5. Версии моделей через API DeepSeek\n\nЗапрос:\n{prompt}")

        results: list[ModelRunResult] = []
        for case in MODEL_CASES:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
            result = await asyncio.to_thread(deepseek.run_model_case, prompt, case)
            results.append(result)
            await reply_long(update, format_model_result(result))

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        comparison = await asyncio.to_thread(deepseek.compare_model_results, prompt, results)
        await reply_long(
            update,
            f"Сравнение от DeepSeek:\n{comparison or 'DeepSeek returned an empty comparison response.'}\n\n{SOURCE_LINKS}",
        )
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
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot is running in polling mode")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
