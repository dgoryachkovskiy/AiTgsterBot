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
DEFAULT_MODEL = "deepseek-v4-pro"

SOLUTION_MAX_TOKENS = 700
PROMPT_DRAFT_MAX_TOKENS = 350
COMPARISON_MAX_TOKENS = 700
DEFAULT_TEMPERATURE = 0.2
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


def truncate_for_prompt(text: str, limit: int = 2000) -> str:
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

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = SOLUTION_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            extra_body=THINKING_DISABLED,
            max_tokens=max_tokens,
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

    def solve_direct(self, task: str) -> str:
        return self.chat(
            "You are a helpful assistant. Answer in Russian. Give the final solution clearly.",
            task,
        )

    def solve_step_by_step(self, task: str) -> str:
        return self.chat(
            "You are a helpful assistant. Answer in Russian.",
            f"Решай пошагово.\n\nЗадача:\n{task}",
        )

    def draft_solution_prompt(self, task: str) -> str:
        return self.chat(
            (
                "You are a prompt engineer. Answer in Russian. "
                "Create one strong prompt for another LLM to solve the task accurately. "
                "Return only the prompt text."
            ),
            f"Задача:\n{task}",
            max_tokens=PROMPT_DRAFT_MAX_TOKENS,
        )

    def solve_with_generated_prompt(self, task: str) -> tuple[str, str]:
        generated_prompt = self.draft_solution_prompt(task)
        if not generated_prompt:
            return "", ""

        solution = self.chat(
            "You are a helpful assistant. Follow the user prompt exactly. Answer in Russian.",
            generated_prompt,
        )
        return generated_prompt, solution

    def solve_with_experts(self, task: str) -> str:
        return self.chat(
            (
                "You are a panel of three experts answering in Russian: "
                "Analyst, Engineer, Critic. Each expert must give a solution. "
                "Then add one short joint conclusion."
            ),
            (
                "Решите одну задачу группой экспертов.\n\n"
                "Формат:\n"
                "Аналитик: ...\n"
                "Инженер: ...\n"
                "Критик: ...\n"
                "Общий вывод: ...\n\n"
                f"Задача:\n{task}"
            ),
        )

    def compare_solutions(
        self,
        task: str,
        direct: str,
        step_by_step: str,
        generated_prompt_solution: str,
        experts: str,
    ) -> str:
        return self.chat(
            (
                "You compare LLM solutions in Russian. "
                "Be concrete. Identify differences and choose the most accurate method."
            ),
            (
                "Сравни четыре способа решения одной задачи.\n\n"
                f"Задача:\n{task}\n\n"
                f"1. Прямой ответ:\n{truncate_for_prompt(direct)}\n\n"
                f"2. Инструкция 'решай пошагово':\n{truncate_for_prompt(step_by_step)}\n\n"
                f"3. Сначала сгенерирован промпт, затем получено решение:\n"
                f"{truncate_for_prompt(generated_prompt_solution)}\n\n"
                f"4. Группа экспертов:\n{truncate_for_prompt(experts)}\n\n"
                "Сравни: отличаются ли ответы, где больше точности, где больше риска ошибки. "
                "В конце выбери самый точный способ."
            ),
            max_tokens=COMPARISON_MAX_TOKENS,
        )

    def run_day3_reasoning_experiment(self, task: str) -> str:
        direct = self.solve_direct(task)
        step_by_step = self.solve_step_by_step(task)
        generated_prompt, generated_prompt_solution = self.solve_with_generated_prompt(task)
        experts = self.solve_with_experts(task)
        comparison = self.compare_solutions(
            task=task,
            direct=direct,
            step_by_step=step_by_step,
            generated_prompt_solution=generated_prompt_solution,
            experts=experts,
        )

        return (
            "День 3. Разные способы рассуждения через API DeepSeek\n\n"
            f"Задача:\n{task}\n\n"
            "1. Прямой ответ без дополнительных инструкций:\n"
            f"{direct or 'DeepSeek returned an empty response.'}\n\n"
            "2. Инструкция 'решай пошагово':\n"
            f"{step_by_step or 'DeepSeek returned an empty response.'}\n\n"
            "3. Модель сначала составила промпт, затем решила задачу:\n\n"
            "Сгенерированный промпт:\n"
            f"{generated_prompt or 'DeepSeek returned an empty prompt.'}\n\n"
            "Решение по сгенерированному промпту:\n"
            f"{generated_prompt_solution or 'DeepSeek returned an empty response.'}\n\n"
            "4. Группа экспертов:\n"
            f"{experts or 'DeepSeek returned an empty response.'}\n\n"
            "Сравнение от DeepSeek:\n"
            f"{comparison or 'DeepSeek returned an empty comparison response.'}"
        )


deepseek = DeepSeekClient()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message:
        await update.message.reply_text(
            "Напишите одну логическую, алгоритмическую или аналитическую задачу. "
            "Бот решит ее четырьмя способами через DeepSeek и сравнит результаты."
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    task = update.message.text.strip()
    if not task:
        await update.message.reply_text("Отправьте непустую задачу.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        answer = await asyncio.to_thread(deepseek.run_day3_reasoning_experiment, task)
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
