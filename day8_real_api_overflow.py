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


SYSTEM_MESSAGE = {
    "role": "system",
    "content": "You are SimpleDeepSeekAgent. Answer in Russian. Keep replies very short.",
}


@dataclass(frozen=True)
class ApiCallResult:
    name: str
    ok: bool
    elapsed_seconds: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cache_hit_tokens: int
    cache_miss_tokens: int
    cost_usd: float
    answer_preview: str
    error: str


def build_context_filler_prompt(repeated_tokens: int = 1_000_000) -> str:
    # Leading-space ASCII word is usually one tokenizer item per repetition.
    # This request should succeed while bringing the dialog close to the limit.
    return "Second request: fill most of the context window." + (" fill" * repeated_tokens)


def build_context_overflow_prompt(repeated_tokens: int = 80_000) -> str:
    # Third request should overflow because the second request is already in history.
    return "Third request: continue the same dialog and exceed remaining context." + (" overflow" * repeated_tokens)


def call_deepseek(
    client: OpenAI,
    model: str,
    name: str,
    messages: list[dict[str, str]],
) -> ApiCallResult:
    started = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=model,
            extra_body=THINKING_DISABLED,
            temperature=0,
            max_tokens=1,
            messages=messages,
        )
    except APIStatusError as error:
        elapsed = time.perf_counter() - started
        body = error.response.text[:1200] if error.response is not None else ""
        return ApiCallResult(
            name=name,
            ok=False,
            elapsed_seconds=elapsed,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            cache_hit_tokens=0,
            cache_miss_tokens=0,
            cost_usd=0.0,
            answer_preview="",
            error=f"{type(error).__name__}: status={error.status_code}; body={body}",
        )
    except APIError as error:
        elapsed = time.perf_counter() - started
        return ApiCallResult(
            name=name,
            ok=False,
            elapsed_seconds=elapsed,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            cache_hit_tokens=0,
            cache_miss_tokens=0,
            cost_usd=0.0,
            answer_preview="",
            error=f"{type(error).__name__}: {error}",
        )

    elapsed = time.perf_counter() - started
    usage = response.usage
    prompt_tokens = usage_value(usage, "prompt_tokens")
    completion_tokens = usage_value(usage, "completion_tokens")
    cache_hit_tokens = usage_value(usage, "prompt_cache_hit_tokens")
    cache_miss_tokens = usage_value(usage, "prompt_cache_miss_tokens")
    cost_usd = estimate_request_cost_usd(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
    )
    answer = ""
    if response.choices:
        answer = response.choices[0].message.content or ""

    return ApiCallResult(
        name=name,
        ok=True,
        elapsed_seconds=elapsed,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=usage_value(usage, "total_tokens"),
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
        cost_usd=cost_usd,
        answer_preview=answer[:200],
        error="",
    )


def format_result(result: ApiCallResult) -> str:
    if result.ok:
        return (
            f"{result.name}: OK за {result.elapsed_seconds:.1f}s\n"
            f"prompt={result.prompt_tokens}, answer={result.completion_tokens}, total={result.total_tokens}\n"
            f"cache_hit={result.cache_hit_tokens}, cache_miss={result.cache_miss_tokens}\n"
            f"cost~${result.cost_usd:.6f}\n"
            f"answer={result.answer_preview!r}"
        )
    return (
        f"{result.name}: ERROR за {result.elapsed_seconds:.1f}s\n"
        "prompt=0, answer=0, total=0, cost~$0.000000\n"
        f"{result.error}"
    )


def run_real_api_overflow_demo(second_repeated_tokens: int = 1_000_000, third_repeated_tokens: int = 80_000) -> str:
    load_dotenv()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    client = OpenAI(api_key=require_env("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL)

    history: list[dict[str, str]] = []

    first_user = "Короткий запрос: запомни, что меня зовут Данил."
    first = call_deepseek(client, model, "1. Короткий API-запрос", [SYSTEM_MESSAGE, {"role": "user", "content": first_user}])
    if first.ok:
        history.extend([{"role": "user", "content": first_user}, {"role": "assistant", "content": first.answer_preview}])

    second_user = build_context_filler_prompt(repeated_tokens=second_repeated_tokens)
    second = call_deepseek(
        client,
        model,
        "2. Длинный API-запрос",
        [SYSTEM_MESSAGE, *history, {"role": "user", "content": second_user}],
    )
    if second.ok:
        history.extend([{"role": "user", "content": second_user}, {"role": "assistant", "content": second.answer_preview}])

    third_user = build_context_overflow_prompt(repeated_tokens=third_repeated_tokens)
    third = call_deepseek(
        client,
        model,
        "3. API-запрос выше лимита модели",
        [SYSTEM_MESSAGE, *history, {"role": "user", "content": third_user}],
    )

    results = [first, second, third]
    lines = [
        "Day 8. Реальный API overflow",
        f"model={model}",
        f"second_request_repeated_tokens={second_repeated_tokens}",
        f"third_request_repeated_tokens={third_repeated_tokens}",
        "max_tokens=1 для всех трех вызовов, чтобы почти убрать output-cost.",
        "Сценарий: 1-й короткий, 2-й успешно почти заполняет историю, 3-й падает из-за накопленного контекста.",
        "",
    ]
    lines.extend(format_result(result) + "\n" for result in results)
    if not third.ok:
        lines.append(
            "Вывод: третий запрос действительно дошел до DeepSeek API и сломался на лимите модели. "
            "После ошибки ответа модели нет, usage не возвращается, диалог продолжать этим запросом нельзя."
        )
    else:
        lines.append(
            "Внимание: третий запрос не переполнил лимит. Увеличьте repeated_tokens и повторите."
        )
    return "\n".join(lines)


async def send_report_to_telegram(report: str, chat_id: str) -> None:
    bot = Bot(token=require_env("TELEGRAM_BOT_TOKEN"))
    for chunk in split_telegram_message(report):
        await bot.send_message(chat_id=chat_id, text=chunk)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--send-telegram", action="store_true")
    parser.add_argument("--chat-id")
    parser.add_argument("--repeated-tokens", type=int, default=None)
    parser.add_argument("--second-repeated-tokens", type=int, default=1_000_000)
    parser.add_argument("--third-repeated-tokens", type=int, default=80_000)
    args = parser.parse_args()
    if args.repeated_tokens is not None:
        args.third_repeated_tokens = args.repeated_tokens

    report = run_real_api_overflow_demo(
        second_repeated_tokens=args.second_repeated_tokens,
        third_repeated_tokens=args.third_repeated_tokens,
    )
    print(report)

    if args.send_telegram:
        if not args.chat_id:
            history_path = os.getenv("BOT_HISTORY_FILE", "chat_history.json")
            with open(history_path, "r", encoding="utf-8") as file:
                history = json.load(file)
            args.chat_id = next(iter(history["chats"].keys()))
        asyncio.run(send_report_to_telegram(report, args.chat_id))
        print(f"sent_real_api_overflow_report_to_chat:{args.chat_id}")


if __name__ == "__main__":
    main()
