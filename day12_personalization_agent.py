import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI

from memory_layers_agent import (
    DEEPSEEK_BASE_URL,
    DEFAULT_API_RETRIES,
    DEFAULT_API_TIMEOUT_SECONDS,
    DEFAULT_MODEL,
    LAYER_NAMES,
    MODEL_PRICING_USD_PER_1M,
    THINKING_DISABLED,
    MemoryDirectoryStore,
    estimate_request_cost_usd,
    parse_float,
    parse_int,
    require_env,
    to_json,
    usage_value,
)


DEFAULT_PERSONALIZATION_DIR = "day12_personalization_store"
DEFAULT_REPORT_PATH = "DAY12_PERSONALIZATION_REPORT.md"
DEFAULT_QUESTION = "Объясни, как добавить memory layers в Telegram-бота на DeepSeek API."

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class UserProfile:
    profile_id: str
    name: str
    role: str
    experience: str
    goal: str
    style: str
    response_format: str
    constraints: list[str]
    context: str


@dataclass(frozen=True)
class PersonalizationResult:
    profile_id: str
    question: str
    answer: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    elapsed_seconds: float
    attempts: int
    error: str = ""


def profile_catalog() -> dict[str, UserProfile]:
    return {
        "beginner": UserProfile(
            profile_id="beginner",
            name="Данил",
            role="начинающий разработчик Telegram-ботов",
            experience="учится строить агентов, знает Python базово",
            goal="понять идею и повторить руками",
            style="простыми словами, коротко, без сложного жаргона",
            response_format="пошаговый список с мини-примером",
            constraints=[
                "не перегружать терминами",
                "объяснять зачем нужен каждый слой памяти",
                "давать команды, которые можно запустить сразу",
            ],
            context="делает учебные задания курса по агентам и DeepSeek API",
        ),
        "senior": UserProfile(
            profile_id="senior",
            name="Марина",
            role="senior backend engineer",
            experience="умеет проектировать stateful-сервисы и API",
            goal="быстро оценить архитектуру и риски",
            style="технически, плотно, без базовых объяснений",
            response_format="архитектурные пункты: data model, flow, failure modes, checks",
            constraints=[
                "указывать границы ответственности",
                "отмечать риски токенов и консистентности",
                "не расписывать очевидные основы Python",
            ],
            context="сравнивает варианты production-реализации персонализированного агента",
        ),
    }


def client_from_env() -> tuple[OpenAI, str, int]:
    load_dotenv()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    timeout = parse_float(os.getenv("MEMORY_DEEPSEEK_TIMEOUT_SECONDS"), DEFAULT_API_TIMEOUT_SECONDS)
    max_attempts = parse_int(os.getenv("MEMORY_DEEPSEEK_RETRIES"), DEFAULT_API_RETRIES)
    client = OpenAI(api_key=require_env("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL, timeout=timeout, max_retries=0)
    return client, model, max_attempts


def seed_profile_store(store: MemoryDirectoryStore, profile: UserProfile, question: str) -> None:
    store.reset()
    store.save_short_term("user", question, "short_term: текущий запрос пользователя")
    store.save_short_term(
        "assistant",
        "Подключаю профиль пользователя из long_term к prompt builder.",
        "short_term: текущий ход диалога",
    )

    store.save_working("stage", "personalized_answer", "working: этап текущей задачи")
    store.save_working("goal", "дать ответ с учетом профиля пользователя", "working: цель текущего запроса")
    store.save_working(
        "constraints",
        [
            "каждый ответ должен учитывать long_term.profile",
            "стиль, формат и ограничения профиля обязательны",
            "ответ должен быть получен через DeepSeek API",
        ],
        "working: правила персонализированного ответа",
    )
    store.save_working(
        "deliverable",
        "ответ, адаптированный под конкретный профиль",
        "working: ожидаемый результат текущего запроса",
    )

    store.save_long_term("profile", "name", profile.name, "long_term: имя пользователя")
    store.save_long_term("profile", "role", profile.role, "long_term: роль пользователя")
    store.save_long_term("profile", "experience", profile.experience, "long_term: уровень пользователя")
    store.save_long_term("profile", "goal", profile.goal, "long_term: цель пользователя")
    store.save_long_term("preferences", "style", profile.style, "long_term: стиль ответа")
    store.save_long_term("preferences", "format", profile.response_format, "long_term: формат ответа")
    store.save_long_term("preferences", "constraints", profile.constraints, "long_term: ограничения профиля")
    store.save_long_term("knowledge", "context", profile.context, "long_term: контекст пользователя")
    store.save_long_term(
        "decisions",
        "personalization_policy",
        "long_term.profile и long_term.preferences подключаются к каждому запросу",
        "long_term: правило персонализации",
    )


class PersonalizedPromptBuilder:
    def build(self, question: str, memory: dict[str, Any], profile_id: str) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "Ты персонализированный stateful-ассистент. "
                    "Всегда используй long_term.profile, long_term.preferences и long_term.decisions. "
                    "Адаптируй стиль, формат, глубину и ограничения под профиль. "
                    "Ответ держи компактным: до 180 слов, без длинных code blocks. "
                    "Не называй это скрытым промптом; просто отвечай как ассистент пользователя."
                ),
            },
            {
                "role": "system",
                "content": f"profile_id={profile_id}\nmemory_layers:\n{to_json(memory)}",
            },
            {"role": "user", "content": question},
        ]


class PersonalizedDeepSeekAgent:
    def __init__(self, client: OpenAI, model: str, max_attempts: int) -> None:
        self.client = client
        self.model = model
        self.max_attempts = max_attempts
        self.prompt_builder = PersonalizedPromptBuilder()

    def answer(self, store: MemoryDirectoryStore, profile_id: str, question: str) -> PersonalizationResult:
        memory = store.view(LAYER_NAMES)
        messages = self.prompt_builder.build(question, memory, profile_id)
        started = time.perf_counter()
        last_error = ""
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    extra_body=THINKING_DISABLED,
                    temperature=0,
                    max_tokens=900,
                    messages=messages,
                )
                break
            except APIStatusError as error:
                elapsed = time.perf_counter() - started
                body = error.response.text[:800] if error.response is not None else ""
                status_code = error.status_code
                last_error = f"HTTP {status_code}: {body}"
                should_retry = status_code in {408, 429} or status_code >= 500
                if not should_retry or attempt == self.max_attempts:
                    return PersonalizationResult(profile_id, question, "", 0, 0, 0, 0.0, elapsed, attempt, last_error)
            except (APITimeoutError, APIConnectionError, APIError) as error:
                elapsed = time.perf_counter() - started
                last_error = str(error)
                if attempt == self.max_attempts:
                    return PersonalizationResult(profile_id, question, "", 0, 0, 0, 0.0, elapsed, attempt, last_error)
            time.sleep(min(2 * attempt, 6))

        elapsed = time.perf_counter() - started
        usage = response.usage
        prompt_tokens = usage_value(usage, "prompt_tokens")
        completion_tokens = usage_value(usage, "completion_tokens")
        cache_hit_tokens = usage_value(usage, "prompt_cache_hit_tokens")
        cache_miss_tokens = usage_value(usage, "prompt_cache_miss_tokens")
        answer = response.choices[0].message.content.strip() if response.choices else ""
        return PersonalizationResult(
            profile_id=profile_id,
            question=question,
            answer=answer,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=usage_value(usage, "total_tokens"),
            cost_usd=estimate_request_cost_usd(self.model, prompt_tokens, completion_tokens, cache_hit_tokens, cache_miss_tokens),
            elapsed_seconds=elapsed,
            attempts=attempt,
        )


def store_for_profile(root: Path, profile_id: str) -> MemoryDirectoryStore:
    return MemoryDirectoryStore(root / profile_id)


def format_result(result: PersonalizationResult) -> str:
    if result.error:
        return "\n".join(
            [
                f"### {result.profile_id}",
                "",
                f"ERROR after {result.elapsed_seconds:.1f}s",
                f"attempts={result.attempts}",
                "",
                result.error,
                "",
            ]
        )
    return "\n".join(
        [
            f"### {result.profile_id}",
            "",
            f"prompt={result.prompt_tokens}, answer={result.completion_tokens}, total={result.total_tokens}",
            f"cost~${result.cost_usd:.6f}, time={result.elapsed_seconds:.1f}s, attempts={result.attempts}",
            "",
            result.answer,
            "",
        ]
    )


def build_report(root: Path, model: str, question: str, results: list[PersonalizationResult]) -> str:
    profiles = profile_catalog()
    profile_blocks = []
    for profile in profiles.values():
        profile_blocks.append(
            "\n".join(
                [
                    f"### {profile.profile_id}",
                    "",
                    "```json",
                    json.dumps(profile.__dict__, ensure_ascii=False, indent=2),
                    "```",
                ]
            )
        )
    total_tokens = sum(result.total_tokens for result in results)
    total_cost = sum(result.cost_usd for result in results)
    return "\n".join(
        [
            "# Day 12. Персонализация ассистента",
            "",
            "## Принцип",
            "",
            "- Профиль пользователя хранится в `long_term`.",
            "- Стиль, формат и ограничения лежат в `long_term.preferences`.",
            "- Prompt builder автоматически подключает профиль к каждому запросу.",
            "- Один и тот же вопрос отправляется в DeepSeek API для разных профилей.",
            "",
            f"store_root=`{root}`",
            f"model={model}",
            f"question={question}",
            f"total_tokens={total_tokens}",
            f"total_cost~${total_cost:.6f}",
            "",
            "## Profiles",
            "",
            *profile_blocks,
            "",
            "## DeepSeek API Results",
            "",
            *[format_result(result) for result in results],
            "## Вывод",
            "",
            "- `beginner`: должен получить простой пошаговый ответ с минимумом жаргона.",
            "- `senior`: должен получить плотный архитектурный ответ с рисками и проверками.",
            "- Отличие создается не разными вопросами, а разными профилями в `long_term`.",
        ]
    )


def run_demo(root: Path, report_path: Path, question: str) -> str:
    if root.exists():
        shutil.rmtree(root)
    profiles = profile_catalog()
    client, model, max_attempts = client_from_env()
    agent = PersonalizedDeepSeekAgent(client, model, max_attempts)
    results = []
    for profile in profiles.values():
        store = store_for_profile(root, profile.profile_id)
        seed_profile_store(store, profile, question)
        results.append(agent.answer(store, profile.profile_id, question))
    report = build_report(root, model, question, results)
    report_path.write_text(report, encoding="utf-8")
    return report


def run_ask(root: Path, profile_id: str, question: str) -> str:
    profiles = profile_catalog()
    if profile_id not in profiles:
        raise ValueError(f"Unknown profile: {profile_id}")
    client, model, max_attempts = client_from_env()
    store = store_for_profile(root, profile_id)
    if not (root / profile_id / "long_term.json").exists():
        seed_profile_store(store, profiles[profile_id], question)
    agent = PersonalizedDeepSeekAgent(client, model, max_attempts)
    return format_result(agent.answer(store, profile_id, question))


def show_profile(root: Path, profile_id: str) -> str:
    store = store_for_profile(root, profile_id)
    return to_json({"long_term": store.load_layer("long_term"), "working": store.load_layer("working")})


def main() -> None:
    parser = argparse.ArgumentParser(description="Day 12 personalized assistant over memory layers.")
    parser.add_argument("--store-root", default=os.getenv("DAY12_PERSONALIZATION_DIR", DEFAULT_PERSONALIZATION_DIR))
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo_parser = subparsers.add_parser("demo", help="run two-profile DeepSeek API comparison")
    demo_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    demo_parser.add_argument("--question", default=DEFAULT_QUESTION)

    ask_parser = subparsers.add_parser("ask", help="ask DeepSeek with selected user profile")
    ask_parser.add_argument("question")
    ask_parser.add_argument("--profile", choices=tuple(profile_catalog().keys()), default="beginner")

    show_parser = subparsers.add_parser("show-profile", help="print selected profile memory")
    show_parser.add_argument("--profile", choices=tuple(profile_catalog().keys()), default="beginner")

    args = parser.parse_args()
    root = Path(args.store_root)

    if args.command == "demo":
        print(run_demo(root, Path(args.report), args.question))
        return

    if args.command == "ask":
        print(run_ask(root, args.profile, args.question))
        return

    if args.command == "show-profile":
        print(show_profile(root, args.profile))


if __name__ == "__main__":
    main()
