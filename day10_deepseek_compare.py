import argparse
import asyncio
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

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
from day10_context_strategies import Day10MemoryStore, SimpleTokenCounter


EXPECTED = {
    "goal": "Telegram-агент поддержки",
    "constraint": "до 2 секунд",
    "decision": "DeepSeek API",
    "stack": "Python",
    "budget": "минимальный",
    "deadline": "пятница",
}

FINAL_QUESTION = (
    "На основе только переданного контекста верни JSON без markdown. "
    "Ключи: goal, constraint, decision, stack, budget, deadline. "
    "Если данных нет в контексте, ставь null."
)


@dataclass(frozen=True)
class DeepSeekStrategyResult:
    strategy: str
    ok: bool
    elapsed_seconds: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    quality_score: int
    context_tokens_estimate: int
    answer: str
    error: str = ""


def scenario_messages() -> list[str]:
    return [
        "цель: Telegram-агент поддержки",
        "ограничение: ответ до 2 секунд",
        "предпочтение: хранить важные решения между запусками",
        "решение: DeepSeek API",
        "договоренность: интерфейс через Telegram команды",
        "стек: Python, python-telegram-bot, JSON",
        "бюджет: минимальный",
        "дедлайн: пятница",
        "Шум: обсуждаем текст приветствия",
        "Шум: обсуждаем название кнопки помощи",
        "Шум: обсуждаем порядок разделов в отчете",
        "Шум: повторяем, что нужно сравнение стратегий",
    ]


def load_json_object(text: str) -> dict[str, object]:
    clean = text.strip()
    clean = re.sub(r"^```(?:json)?", "", clean).strip()
    clean = re.sub(r"```$", "", clean).strip()
    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def quality_score(answer: str) -> int:
    parsed = load_json_object(answer)
    if parsed:
        score = 0
        for key, expected in EXPECTED.items():
            value = parsed.get(key)
            if isinstance(value, str) and expected.lower() in value.lower():
                score += 1
        return score

    lowered = answer.lower()
    return sum(1 for expected in EXPECTED.values() if expected.lower() in lowered)


def call_deepseek(
    client: OpenAI,
    model: str,
    strategy: str,
    context: list[dict[str, str]],
) -> DeepSeekStrategyResult:
    messages = [
        {
            "role": "system",
            "content": "Use only provided context. Return compact JSON. Do not invent missing details.",
        },
        *context,
        {"role": "user", "content": FINAL_QUESTION},
    ]
    context_tokens_estimate = SimpleTokenCounter().count_messages(context)
    started = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=model,
            extra_body=THINKING_DISABLED,
            temperature=0,
            max_tokens=260,
            messages=messages,
        )
    except APIStatusError as error:
        elapsed = time.perf_counter() - started
        body = error.response.text[:800] if error.response is not None else ""
        return DeepSeekStrategyResult(strategy, False, elapsed, 0, 0, 0, 0.0, 0, context_tokens_estimate, "", body)
    except APIError as error:
        elapsed = time.perf_counter() - started
        return DeepSeekStrategyResult(strategy, False, elapsed, 0, 0, 0, 0.0, 0, context_tokens_estimate, "", str(error))

    elapsed = time.perf_counter() - started
    usage = response.usage
    prompt_tokens = usage_value(usage, "prompt_tokens")
    completion_tokens = usage_value(usage, "completion_tokens")
    cache_hit_tokens = usage_value(usage, "prompt_cache_hit_tokens")
    cache_miss_tokens = usage_value(usage, "prompt_cache_miss_tokens")
    answer = response.choices[0].message.content.strip() if response.choices else ""
    return DeepSeekStrategyResult(
        strategy=strategy,
        ok=True,
        elapsed_seconds=elapsed,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=usage_value(usage, "total_tokens"),
        cost_usd=estimate_request_cost_usd(model, prompt_tokens, completion_tokens, cache_hit_tokens, cache_miss_tokens),
        quality_score=quality_score(answer),
        context_tokens_estimate=context_tokens_estimate,
        answer=answer,
    )


def build_contexts(recent_messages: int = 6) -> dict[str, list[dict[str, str]]]:
    path = Path(tempfile.gettempdir()) / "day10_deepseek_compare.json"
    path.unlink(missing_ok=True)
    scenario = scenario_messages()

    contexts: dict[str, list[dict[str, str]]] = {}
    for strategy in ["sliding", "facts"]:
        store = Day10MemoryStore(path, recent_messages=recent_messages)
        store.enable(1, strategy)
        for item in scenario:
            store.append_exchange(1, item, "Запомнил.")
        contexts[strategy] = store.build_history(1)
        path.unlink(missing_ok=True)

    store = Day10MemoryStore(path, recent_messages=recent_messages)
    store.enable(1, "branching")
    for item in scenario[:8]:
        store.append_exchange(1, item, "Запомнил.")
    store.create_checkpoint(1, "base")
    store.create_branch(1, "cheap", "base")
    store.append_exchange(1, "решение: экономим токены и держим краткий контекст", "Запомнил.")
    contexts["branching/cheap"] = store.build_history(1)
    store.create_branch(1, "quality", "base")
    store.append_exchange(1, "решение: сохраняем максимум требований для стабильности", "Запомнил.")
    contexts["branching/quality"] = store.build_history(1)
    path.unlink(missing_ok=True)
    return contexts


def format_result(result: DeepSeekStrategyResult) -> str:
    if not result.ok:
        return (
            f"{result.strategy}\n"
            f"ERROR за {result.elapsed_seconds:.1f}s\n"
            f"context_tokens_est~{result.context_tokens_estimate}\n"
            f"{result.error}"
        )
    return (
        f"{result.strategy}\n"
        f"quality={result.quality_score}/6\n"
        f"context_tokens_est~{result.context_tokens_estimate}\n"
        f"prompt={result.prompt_tokens}, answer={result.completion_tokens}, total={result.total_tokens}\n"
        f"cost~${result.cost_usd:.6f}, time={result.elapsed_seconds:.1f}s\n"
        f"answer={result.answer}"
    )


def build_conclusion_prompt(results: list[DeepSeekStrategyResult]) -> str:
    payload = [
        {
            "strategy": result.strategy,
            "ok": result.ok,
            "quality_score": result.quality_score,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
            "cost_usd": result.cost_usd,
            "context_tokens_estimate": result.context_tokens_estimate,
            "answer": result.answer,
            "error": result.error,
        }
        for result in results
    ]
    return (
        "Ты сравниваешь стратегии управления контекстом агента: Sliding Window, Sticky Facts, Branching.\n"
        "Ниже реальные ответы DeepSeek по одному сценарию сбора ТЗ.\n"
        "Сделай краткий вывод на русском: качество, стабильность, расход токенов, удобство пользователя, лучшая стратегия.\n"
        "Не повторяй весь JSON, дай именно аналитический вывод.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def call_deepseek_conclusion(client: OpenAI, model: str, results: list[DeepSeekStrategyResult]) -> str:
    response = client.chat.completions.create(
        model=model,
        extra_body=THINKING_DISABLED,
        temperature=0,
        max_tokens=360,
        messages=[
            {
                "role": "system",
                "content": "You are an evaluator. Analyze experiment results and write concise Russian conclusions.",
            },
            {"role": "user", "content": build_conclusion_prompt(results)},
        ],
    )
    return response.choices[0].message.content.strip() if response.choices else ""


def build_day10_deepseek_report(recent_messages: int = 6) -> str:
    load_dotenv()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    client = OpenAI(api_key=require_env("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL)
    contexts = build_contexts(recent_messages=recent_messages)
    results = [call_deepseek(client, model, strategy, context) for strategy, context in contexts.items()]
    lines = [
        "День 10. Сравнение стратегий через DeepSeek",
        f"model={model}",
        f"recent_messages={recent_messages}",
        "Сценарий: 12 сообщений собирают ТЗ, финальный вопрос одинаковый.",
        "",
    ]
    lines.extend(format_result(result) + "\n" for result in results)
    lines.append("Вывод: это реальные ответы DeepSeek, не локальная эвристика.")
    return "\n".join(lines)


def build_day10_deepseek_report(recent_messages: int = 6) -> str:
    load_dotenv()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    client = OpenAI(api_key=require_env("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL)
    contexts = build_contexts(recent_messages=recent_messages)
    results = [call_deepseek(client, model, strategy, context) for strategy, context in contexts.items()]
    conclusion = call_deepseek_conclusion(client, model, results)
    lines = [
        "День 10. Сравнение стратегий через DeepSeek",
        f"model={model}",
        f"recent_messages={recent_messages}",
        "Сценарий: 12 сообщений собирают ТЗ, финальный вопрос одинаковый.",
        "",
    ]
    lines.extend(format_result(result) + "\n" for result in results)
    lines.extend(["Вывод DeepSeek:", conclusion])
    return "\n".join(lines)


def build_conclusion_prompt(results: list[DeepSeekStrategyResult]) -> str:
    grouped: list[dict[str, object]] = []
    branching_branches = []
    for result in results:
        payload = {
            "name": result.strategy,
            "ok": result.ok,
            "quality_score": result.quality_score,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
            "cost_usd": result.cost_usd,
            "context_tokens_estimate": result.context_tokens_estimate,
            "answer": result.answer,
            "error": result.error,
        }
        if result.strategy.startswith("branching/"):
            branching_branches.append(payload)
        else:
            grouped.append({"strategy": result.strategy, **payload})
    if branching_branches:
        grouped.append(
            {
                "strategy": "branching",
                "note": "One Branching strategy with two independent branches from one checkpoint.",
                "branches": branching_branches,
                "quality_score": max(branch["quality_score"] for branch in branching_branches),
                "total_tokens": sum(branch["total_tokens"] for branch in branching_branches),
            }
        )
    return (
        "Сравни ровно 3 стратегии управления контекстом: Sliding Window, Sticky Facts, Branching.\n"
        "Важно: branching/cheap и branching/quality - это две ветки одной стратегии Branching, не отдельные стратегии.\n"
        "Ниже реальные ответы DeepSeek по одному сценарию сбора ТЗ.\n"
        "Сделай краткий вывод на русском: качество, стабильность, расход токенов, удобство пользователя, лучшая стратегия.\n"
        "Не повторяй весь JSON, дай именно аналитический вывод.\n\n"
        + json.dumps(grouped, ensure_ascii=False, indent=2)
    )


def format_branch_result(result: DeepSeekStrategyResult) -> str:
    branch_name = result.strategy.split("/", 1)[1] if "/" in result.strategy else result.strategy
    if not result.ok:
        return (
            f"  branch={branch_name}: ERROR за {result.elapsed_seconds:.1f}s\n"
            f"  context_tokens_est~{result.context_tokens_estimate}\n"
            f"  {result.error}"
        )
    return (
        f"  branch={branch_name}\n"
        f"  quality={result.quality_score}/6\n"
        f"  context_tokens_est~{result.context_tokens_estimate}\n"
        f"  prompt={result.prompt_tokens}, answer={result.completion_tokens}, total={result.total_tokens}\n"
        f"  cost~${result.cost_usd:.6f}, time={result.elapsed_seconds:.1f}s\n"
        f"  answer={result.answer}"
    )


def format_grouped_results(results: list[DeepSeekStrategyResult]) -> list[str]:
    lines: list[str] = []
    branching_results = []
    for result in results:
        if result.strategy.startswith("branching/"):
            branching_results.append(result)
        else:
            lines.append(format_result(result) + "\n")
    if branching_results:
        lines.append("branching (one strategy, two branches from one checkpoint)")
        lines.extend(format_branch_result(result) for result in branching_results)
        lines.append("")
    return lines


def build_day10_deepseek_report(recent_messages: int = 6) -> str:
    load_dotenv()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    client = OpenAI(api_key=require_env("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL)
    contexts = build_contexts(recent_messages=recent_messages)
    results = [call_deepseek(client, model, strategy, context) for strategy, context in contexts.items()]
    conclusion = call_deepseek_conclusion(client, model, results)
    lines = [
        "День 10. Сравнение стратегий через DeepSeek",
        f"model={model}",
        f"recent_messages={recent_messages}",
        "Сценарий: 12 сообщений собирают ТЗ, финальный вопрос одинаковый.",
        "Стратегий ровно 3: sliding, facts, branching. У branching внутри 2 ветки.",
        "",
    ]
    lines.extend(format_grouped_results(results))
    lines.extend(["Вывод DeepSeek:", conclusion])
    return "\n".join(lines)


async def send_report_to_telegram(report: str, chat_id: str) -> None:
    bot = Bot(token=require_env("TELEGRAM_BOT_TOKEN"))
    for chunk in split_telegram_message(report):
        await bot.send_message(chat_id=chat_id, text=chunk)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--send-telegram", action="store_true")
    parser.add_argument("--chat-id")
    parser.add_argument("--recent-messages", type=int, default=6)
    args = parser.parse_args()
    report = build_day10_deepseek_report(recent_messages=args.recent_messages)
    print(report)
    if args.send_telegram:
        if not args.chat_id:
            history_path = os.getenv("BOT_HISTORY_FILE", "chat_history.json")
            with open(history_path, "r", encoding="utf-8") as file:
                history = json.load(file)
            args.chat_id = next(iter(history["chats"].keys()))
        asyncio.run(send_report_to_telegram(report, args.chat_id))
        print(f"sent_day10_deepseek_report_to_chat:{args.chat_id}")


if __name__ == "__main__":
    main()
