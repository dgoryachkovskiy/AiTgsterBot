import argparse
import asyncio
import os
import time
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import APIError, APIStatusError, OpenAI
from telegram import Bot

from bot import (
    DEEPSEEK_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_RECENT_MESSAGES,
    DEFAULT_SUMMARY_BATCH_SIZE,
    THINKING_DISABLED,
    TokenCounter,
    estimate_request_cost_usd,
    require_env,
    split_telegram_message,
    usage_value,
)


EXPECTED = {
    "name": "Данил",
    "project": "AiTgsterBot",
    "deadline": "пятница",
}

FINAL_QUESTION = (
    "На основе только переданного контекста верни JSON без markdown. "
    "Ключи: name, project, deadline. Если данных нет в контексте, ставь null."
)


@dataclass(frozen=True)
class Day9DeepSeekResult:
    mode: str
    ok: bool
    elapsed_seconds: float
    context_tokens_estimate: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    quality_score: int
    answer: str
    details: str
    error: str = ""


def build_dialog() -> list[dict[str, str]]:
    messages = [
        {"role": "user", "content": "FACT: пользователя зовут Данил."},
        {"role": "assistant", "content": "Запомнил имя пользователя: Данил."},
        {"role": "user", "content": "FACT: проект называется AiTgsterBot."},
        {"role": "assistant", "content": "Запомнил проект: AiTgsterBot."},
        {"role": "user", "content": "FACT: дедлайн демонстрации в пятницу."},
        {"role": "assistant", "content": "Запомнил дедлайн: пятница."},
    ]
    for index in range(1, 31):
        messages.extend(
            [
                {
                    "role": "user",
                    "content": (
                        f"Шум {index}: обсуждаем polling, JSON, токены, стоимость, "
                        "ошибки API, summary и формат отчета."
                    ),
                },
                {"role": "assistant", "content": f"Принял рабочую деталь {index}."},
            ]
        )
    return messages


def make_summary(old_summary: str, messages: list[dict[str, str]]) -> str:
    facts = [line.strip() for line in old_summary.splitlines() if line.strip()]
    for message in messages:
        content = message["content"]
        if content.startswith("FACT:"):
            facts.append(content.replace("FACT:", "SUMMARY:", 1).strip())
    unique: list[str] = []
    seen: set[str] = set()
    for fact in facts:
        if fact not in seen:
            seen.add(fact)
            unique.append(fact)
    return "\n".join(unique)


def compress_history(
    messages: list[dict[str, str]],
    recent_messages: int = DEFAULT_RECENT_MESSAGES,
    batch_size: int = DEFAULT_SUMMARY_BATCH_SIZE,
) -> tuple[str, list[dict[str, str]], int]:
    summary = ""
    remaining = list(messages)
    compressed = 0
    while len(remaining) > recent_messages:
        old_count = len(remaining) - recent_messages
        batch = remaining[: min(batch_size, old_count)]
        summary = make_summary(summary, batch)
        remaining = remaining[len(batch) :]
        compressed += len(batch)
    return summary, remaining, compressed


def build_contexts() -> dict[str, tuple[list[dict[str, str]], str]]:
    dialog = build_dialog()
    summary, recent_messages, compressed = compress_history(dialog)
    return {
        "full_history": (
            dialog,
            f"messages={len(dialog)}, summary=0",
        ),
        "last_n_without_summary": (
            dialog[-DEFAULT_RECENT_MESSAGES:],
            f"messages={DEFAULT_RECENT_MESSAGES}, dropped={len(dialog) - DEFAULT_RECENT_MESSAGES}",
        ),
        "summary_plus_recent": (
            [
                {"role": "system", "content": "Summary of earlier dialog:\n" + summary},
                *recent_messages,
            ],
            f"summary_chars={len(summary)}, recent={len(recent_messages)}, compressed={compressed}",
        ),
    }


def score_answer(answer: str) -> int:
    lowered = answer.lower()
    return sum(1 for value in EXPECTED.values() if value.lower() in lowered)


def call_deepseek(
    client: OpenAI,
    model: str,
    mode: str,
    context: list[dict[str, str]],
    details: str,
) -> Day9DeepSeekResult:
    messages = [
        {
            "role": "system",
            "content": "Use only provided context. Return compact JSON. Do not invent missing details.",
        },
        *context,
        {"role": "user", "content": FINAL_QUESTION},
    ]
    context_tokens_estimate = TokenCounter().count_messages(context)
    started = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=model,
            extra_body=THINKING_DISABLED,
            temperature=0,
            max_tokens=160,
            messages=messages,
        )
    except APIStatusError as error:
        elapsed = time.perf_counter() - started
        body = error.response.text[:800] if error.response is not None else ""
        return Day9DeepSeekResult(mode, False, elapsed, context_tokens_estimate, 0, 0, 0, 0.0, 0, "", details, body)
    except APIError as error:
        elapsed = time.perf_counter() - started
        return Day9DeepSeekResult(
            mode, False, elapsed, context_tokens_estimate, 0, 0, 0, 0.0, 0, "", details, str(error)
        )

    elapsed = time.perf_counter() - started
    usage = response.usage
    prompt_tokens = usage_value(usage, "prompt_tokens")
    completion_tokens = usage_value(usage, "completion_tokens")
    cache_hit_tokens = usage_value(usage, "prompt_cache_hit_tokens")
    cache_miss_tokens = usage_value(usage, "prompt_cache_miss_tokens")
    answer = response.choices[0].message.content.strip() if response.choices else ""
    return Day9DeepSeekResult(
        mode=mode,
        ok=True,
        elapsed_seconds=elapsed,
        context_tokens_estimate=context_tokens_estimate,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=usage_value(usage, "total_tokens"),
        cost_usd=estimate_request_cost_usd(model, prompt_tokens, completion_tokens, cache_hit_tokens, cache_miss_tokens),
        quality_score=score_answer(answer),
        answer=answer,
        details=details,
    )


def format_result(result: Day9DeepSeekResult) -> str:
    if not result.ok:
        return "\n".join(
            [
                result.mode,
                "ERROR",
                f"time={result.elapsed_seconds:.1f}s",
                result.error,
                "",
            ]
        )
    return "\n".join(
        [
            result.mode,
            f"quality={result.quality_score}/3",
            f"context_tokens_est~{result.context_tokens_estimate}",
            f"prompt={result.prompt_tokens}, answer={result.completion_tokens}, total={result.total_tokens}",
            f"cost~${result.cost_usd:.6f}, time={result.elapsed_seconds:.1f}s",
            f"answer={result.answer}",
            result.details,
            "",
        ]
    )


def build_conclusion_prompt(results: list[Day9DeepSeekResult]) -> str:
    blocks = "\n\n".join(format_result(result) for result in results)
    return (
        "Сделай короткий вывод по эксперименту Day 9. "
        "Сравни качество без сжатия, last-N без summary, summary+recent, расход токенов и удобство. "
        "Назови лучшую стратегию.\n\n"
        f"{blocks}"
    )


def call_deepseek_conclusion(client: OpenAI, model: str, results: list[Day9DeepSeekResult]) -> str:
    response = client.chat.completions.create(
        model=model,
        extra_body=THINKING_DISABLED,
        temperature=0,
        max_tokens=360,
        messages=[{"role": "user", "content": build_conclusion_prompt(results)}],
    )
    return response.choices[0].message.content.strip() if response.choices else "DeepSeek returned an empty conclusion."


def build_day9_deepseek_report() -> str:
    load_dotenv()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    client = OpenAI(api_key=require_env("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL)
    contexts = build_contexts()
    results = [call_deepseek(client, model, mode, context, details) for mode, (context, details) in contexts.items()]
    conclusion = call_deepseek_conclusion(client, model, results)
    full_tokens = next(result.total_tokens for result in results if result.mode == "full_history")
    compressed_tokens = next(result.total_tokens for result in results if result.mode == "summary_plus_recent")
    savings = 100 - (compressed_tokens / full_tokens * 100) if full_tokens else 0.0
    lines = [
        "День 9. Сжатие истории через DeepSeek API",
        f"model={model}",
        f"recent_messages={DEFAULT_RECENT_MESSAGES}, batch={DEFAULT_SUMMARY_BATCH_SIZE}",
        "Сценарий: ранние факты + длинный шумный диалог + одинаковый финальный вопрос.",
        "Все ответы ниже получены через DeepSeek API.",
        "",
    ]
    for result in results:
        lines.append(format_result(result))
    lines.extend(
        [
            f"Экономия summary+recent против full_history по total API tokens: {savings:.1f}%",
            "",
            "Вывод DeepSeek:",
            conclusion,
        ]
    )
    return "\n".join(lines)


async def send_report_to_telegram(report: str, chat_id: str) -> None:
    bot = Bot(require_env("TELEGRAM_BOT_TOKEN"))
    for chunk in split_telegram_message(report):
        await bot.send_message(chat_id=chat_id, text=chunk)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Day 9 compression comparison through DeepSeek API.")
    parser.add_argument("--send-telegram", action="store_true")
    parser.add_argument("--chat-id", default=os.getenv("TELEGRAM_CHAT_ID", "439057315"))
    args = parser.parse_args()

    report = build_day9_deepseek_report()
    print(report)
    if args.send_telegram:
        asyncio.run(send_report_to_telegram(report, args.chat_id))
        print(f"sent_day9_deepseek_report_to_chat:{args.chat_id}")


if __name__ == "__main__":
    main()
