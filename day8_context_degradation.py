import argparse
import asyncio
import json
import os
import time
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import APIError, APIStatusError, OpenAI
from telegram import Bot

from bot import (
    DEEPSEEK_BASE_URL,
    DEFAULT_MODEL,
    THINKING_DISABLED,
    estimate_request_cost_usd,
    require_env,
    split_telegram_message,
    usage_value,
)


TARGET_CODE = "ZEBRA-4816"


@dataclass(frozen=True)
class DegradationResult:
    name: str
    ok: bool
    elapsed_seconds: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cache_hit_tokens: int
    cache_miss_tokens: int
    cost_usd: float
    answer: str
    error: str


def build_noisy_context(decoy_count: int) -> str:
    middle = decoy_count // 2
    lines = [
        "Large memory dump. Most records are decoys.",
        "Task for future question: recover the value of IMPORTANT_TARGET_CODE.",
    ]
    for index in range(decoy_count):
        if index == middle:
            lines.append(f"IMPORTANT_TARGET_CODE = {TARGET_CODE}. This is the only target value.")
        else:
            lines.append(
                f"Noise record {index:06d}: candidate code DECOY-{index % 10000:04d}. "
                "This is not IMPORTANT_TARGET_CODE. Ignore this decoy."
            )
    lines.append("End of memory dump.")
    return "\n".join(lines)


def call_api(client: OpenAI, model: str, name: str, messages: list[dict[str, str]], max_tokens: int) -> DegradationResult:
    started = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=model,
            extra_body=THINKING_DISABLED,
            temperature=0,
            max_tokens=max_tokens,
            messages=messages,
        )
    except APIStatusError as error:
        elapsed = time.perf_counter() - started
        body = error.response.text[:1200] if error.response is not None else ""
        return DegradationResult(name, False, elapsed, 0, 0, 0, 0, 0, 0.0, "", f"status={error.status_code}; {body}")
    except APIError as error:
        elapsed = time.perf_counter() - started
        return DegradationResult(name, False, elapsed, 0, 0, 0, 0, 0, 0.0, "", str(error))

    elapsed = time.perf_counter() - started
    usage = response.usage
    prompt_tokens = usage_value(usage, "prompt_tokens")
    completion_tokens = usage_value(usage, "completion_tokens")
    cache_hit_tokens = usage_value(usage, "prompt_cache_hit_tokens")
    cache_miss_tokens = usage_value(usage, "prompt_cache_miss_tokens")
    answer = response.choices[0].message.content.strip() if response.choices else ""
    return DegradationResult(
        name=name,
        ok=True,
        elapsed_seconds=elapsed,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=usage_value(usage, "total_tokens"),
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
        cost_usd=estimate_request_cost_usd(model, prompt_tokens, completion_tokens, cache_hit_tokens, cache_miss_tokens),
        answer=answer,
        error="",
    )


def format_result(result: DegradationResult) -> str:
    if not result.ok:
        return f"{result.name}: ERROR за {result.elapsed_seconds:.1f}s\n{result.error}"
    return (
        f"{result.name}: OK за {result.elapsed_seconds:.1f}s\n"
        f"prompt={result.prompt_tokens}, answer={result.completion_tokens}, total={result.total_tokens}\n"
        f"cache_hit={result.cache_hit_tokens}, cache_miss={result.cache_miss_tokens}\n"
        f"cost~${result.cost_usd:.6f}\n"
        f"answer={result.answer!r}"
    )


def run_context_degradation_demo(decoy_count: int = 20_000) -> str:
    load_dotenv()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    client = OpenAI(api_key=require_env("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL)
    system = {
        "role": "system",
        "content": "Answer exactly. If asked for a code, return only the code and no extra words.",
    }

    short_messages = [
        system,
        {"role": "user", "content": f"IMPORTANT_TARGET_CODE = {TARGET_CODE}. Remember it."},
        {"role": "assistant", "content": "OK"},
        {"role": "user", "content": "Return IMPORTANT_TARGET_CODE. Only code."},
    ]
    short = call_api(client, model, "1. Short control", short_messages, max_tokens=16)

    first_messages = [
        system,
        {"role": "user", "content": "We will test long-context retrieval in the next messages. Reply OK."},
    ]
    first = call_api(client, model, "2. Start long dialog", first_messages, max_tokens=4)

    noisy_context = build_noisy_context(decoy_count)
    second_messages = [
        system,
        {"role": "user", "content": "We will test long-context retrieval in the next messages. Reply OK."},
        {"role": "assistant", "content": first.answer or "OK"},
        {"role": "user", "content": noisy_context},
    ]
    second = call_api(client, model, "3. Fill long context", second_messages, max_tokens=4)

    third_messages = [
        *second_messages,
        {"role": "assistant", "content": second.answer or "OK"},
        {"role": "user", "content": "Return IMPORTANT_TARGET_CODE from the memory dump. Only code."},
    ]
    third = call_api(client, model, "4. Ask after long context", third_messages, max_tokens=16)

    exact_short = short.answer.strip() == TARGET_CODE
    exact_long = third.answer.strip() == TARGET_CODE
    lines = [
        "Day 8. Контекст не падает, но поведение портится",
        f"model={model}",
        f"target={TARGET_CODE}",
        f"decoy_count={decoy_count}",
        "",
        format_result(short),
        "",
        format_result(first),
        "",
        format_result(second),
        "",
        format_result(third),
        "",
        f"short_exact={exact_short}",
        f"long_exact={exact_long}",
    ]
    if third.ok and not exact_long:
        lines.append("Вывод: API не упал, но на длинном шумном контексте модель не вернула ранний/средний факт exact.")
    elif third.ok:
        lines.append("Вывод: API не упал, и модель нашла факт. Увеличьте --decoy-count или усложните шум для демонстрации деградации.")
    else:
        lines.append("Вывод: этот прогон сломался API-ошибкой, а не деградацией качества.")
    return "\n".join(lines)


async def send_report_to_telegram(report: str, chat_id: str) -> None:
    bot = Bot(token=require_env("TELEGRAM_BOT_TOKEN"))
    for chunk in split_telegram_message(report):
        await bot.send_message(chat_id=chat_id, text=chunk)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decoy-count", type=int, default=20_000)
    parser.add_argument("--send-telegram", action="store_true")
    parser.add_argument("--chat-id")
    args = parser.parse_args()

    report = run_context_degradation_demo(decoy_count=args.decoy_count)
    print(report)

    if args.send_telegram:
        if not args.chat_id:
            history_path = os.getenv("BOT_HISTORY_FILE", "chat_history.json")
            with open(history_path, "r", encoding="utf-8") as file:
                history = json.load(file)
            args.chat_id = next(iter(history["chats"].keys()))
        asyncio.run(send_report_to_telegram(report, args.chat_id))
        print(f"sent_context_degradation_report_to_chat:{args.chat_id}")


if __name__ == "__main__":
    main()
