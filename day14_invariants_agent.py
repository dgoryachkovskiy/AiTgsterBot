import argparse
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI

from memory_layers_agent import (
    DEEPSEEK_BASE_URL,
    DEFAULT_API_RETRIES,
    DEFAULT_API_TIMEOUT_SECONDS,
    DEFAULT_MODEL,
    THINKING_DISABLED,
    estimate_request_cost_usd,
    parse_float,
    parse_int,
    require_env,
    to_json,
    usage_value,
)


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


DEFAULT_STORE_DIR = "day14_invariants_store"
DEFAULT_REPORT_PATH = "DAY14_INVARIANTS_REPORT.md"


@dataclass(frozen=True)
class Invariant:
    invariant_id: str
    category: str
    rule: str
    forbidden_patterns: list[str]
    safe_alternative: str


@dataclass(frozen=True)
class InvariantCheck:
    ok: bool
    violations: list[dict[str, str]]


@dataclass(frozen=True)
class DeepSeekInvariantResult:
    label: str
    request: str
    check: InvariantCheck
    answer: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    elapsed_seconds: float
    attempts: int
    error: str = ""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_invariants() -> list[Invariant]:
    return [
        Invariant(
            invariant_id="architecture-memory-layers",
            category="выбранная архитектура",
            rule="Архитектура должна сохранять memory layers: short_term, working, long_term.",
            forbidden_patterns=["один общий prompt", "все в один промпт", "без memory layers", "убрать слои"],
            safe_alternative="Оставить отдельные слои памяти и выбирать, какие слои подмешивать в prompt.",
        ),
        Invariant(
            invariant_id="stack-python-telegram-deepseek",
            category="ограничения по стеку",
            rule="Стек проекта: Python, python-telegram-bot, DeepSeek API, JSON storage для учебного демо.",
            forbidden_patterns=["node.js", "express", "nestjs", "firebase", "auth0", "supabase"],
            safe_alternative="Реализовать в Python через python-telegram-bot и DeepSeek API.",
        ),
        Invariant(
            invariant_id="security-no-plain-codes",
            category="бизнес-правило / безопасность",
            rule="Нельзя хранить одноразовые коды или токены в открытом виде.",
            forbidden_patterns=["в открытом виде", "plain text", "без хеша", "не хешировать", "храни коды как есть"],
            safe_alternative="Хранить только хеш кода, TTL, попытки и время истечения.",
        ),
        Invariant(
            invariant_id="process-state-machine",
            category="принятое техническое решение",
            rule="Task state machine обязателен: planning -> execution -> validation -> done.",
            forbidden_patterns=["без state machine", "пропусти planning", "сразу execution", "без validation"],
            safe_alternative="Соблюдать переходы planning -> execution -> validation -> done.",
        ),
    ]


class InvariantStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.dialog_path = self.root / "dialog.json"
        self.invariants_path = self.root / "invariants.json"
        self.checks_path = self.root / "checks.json"
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.invariants_path.exists():
            self.reset()

    def reset(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.write_json(
            self.dialog_path,
            {
                "description": "Диалог хранится отдельно от инвариантов.",
                "messages": [],
            },
        )
        self.write_json(
            self.invariants_path,
            {
                "description": "Инварианты и ограничения состояния. Не являются диалогом.",
                "invariants": [invariant.__dict__ for invariant in default_invariants()],
            },
        )
        self.write_json(self.checks_path, {"checks": []})

    def read_json(self, path: Path) -> Any:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def write_json(self, path: Path, data: Any) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        tmp.replace(path)

    def invariants(self) -> list[Invariant]:
        raw = self.read_json(self.invariants_path)["invariants"]
        return [Invariant(**item) for item in raw]

    def append_dialog(self, role: str, content: str) -> None:
        dialog = self.read_json(self.dialog_path)
        dialog["messages"].append({"role": role, "content": content, "time": now_iso()})
        self.write_json(self.dialog_path, dialog)

    def log_check(self, label: str, request: str, check: InvariantCheck) -> None:
        checks = self.read_json(self.checks_path)
        checks["checks"].append(
            {
                "time": now_iso(),
                "label": label,
                "request": request,
                "ok": check.ok,
                "violations": check.violations,
            }
        )
        self.write_json(self.checks_path, checks)

    def snapshot(self) -> dict[str, Any]:
        return {
            "dialog_file": str(self.dialog_path),
            "invariants_file": str(self.invariants_path),
            "checks_file": str(self.checks_path),
            "invariants": self.read_json(self.invariants_path)["invariants"],
        }


class InvariantChecker:
    def __init__(self, invariants: list[Invariant]) -> None:
        self.invariants = invariants

    def check_request(self, request: str) -> InvariantCheck:
        lowered = request.lower()
        violations = []
        for invariant in self.invariants:
            for pattern in invariant.forbidden_patterns:
                if re.search(re.escape(pattern.lower()), lowered):
                    violations.append(
                        {
                            "invariant_id": invariant.invariant_id,
                            "category": invariant.category,
                            "rule": invariant.rule,
                            "matched_pattern": pattern,
                            "safe_alternative": invariant.safe_alternative,
                        }
                    )
                    break
        return InvariantCheck(ok=not violations, violations=violations)


class InvariantPromptBuilder:
    def build(self, request: str, invariants: list[Invariant], check: InvariantCheck) -> list[dict[str, str]]:
        policy = {
            "must_refuse": not check.ok,
            "violations": check.violations,
            "invariants": [invariant.__dict__ for invariant in invariants],
        }
        return [
            {
                "role": "system",
                "content": (
                    "Ты ассистент, который обязан соблюдать инварианты состояния. "
                    "Инварианты важнее пользовательского запроса. "
                    "Если invariant_policy.must_refuse=false, не отказывайся: поставь status=allowed и предложи решение в рамках инвариантов. "
                    "Если упоминаешь state machine, используй порядок planning -> execution -> validation -> done и не добавляй обратные переходы. "
                    "Если запрос конфликтует с инвариантом, откажись от нарушающей части, объясни причину, "
                    "и предложи безопасную альтернативу в рамках инвариантов. "
                    "Ответь коротко JSON без markdown: status, considered_invariants, refusal_reason, safe_alternative."
                ),
            },
            {"role": "system", "content": "invariant_policy:\n" + to_json(policy)},
            {"role": "user", "content": request},
        ]


class DeepSeekInvariantAgent:
    def __init__(self, client: OpenAI, model: str, max_attempts: int) -> None:
        self.client = client
        self.model = model
        self.max_attempts = max_attempts
        self.prompt_builder = InvariantPromptBuilder()

    def answer(
        self,
        label: str,
        request: str,
        invariants: list[Invariant],
        check: InvariantCheck,
    ) -> DeepSeekInvariantResult:
        messages = self.prompt_builder.build(request, invariants, check)
        started = time.perf_counter()
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    extra_body=THINKING_DISABLED,
                    temperature=0,
                    max_tokens=360,
                    messages=messages,
                )
                break
            except APIStatusError as error:
                elapsed = time.perf_counter() - started
                body = error.response.text[:800] if error.response is not None else ""
                status_code = error.status_code
                should_retry = status_code in {408, 429} or status_code >= 500
                if not should_retry or attempt == self.max_attempts:
                    return DeepSeekInvariantResult(label, request, check, "", 0, 0, 0, 0.0, elapsed, attempt, body)
            except (APITimeoutError, APIConnectionError, APIError) as error:
                elapsed = time.perf_counter() - started
                if attempt == self.max_attempts:
                    return DeepSeekInvariantResult(label, request, check, "", 0, 0, 0, 0.0, elapsed, attempt, str(error))
            time.sleep(min(2 * attempt, 6))

        elapsed = time.perf_counter() - started
        usage = response.usage
        prompt_tokens = usage_value(usage, "prompt_tokens")
        completion_tokens = usage_value(usage, "completion_tokens")
        cache_hit_tokens = usage_value(usage, "prompt_cache_hit_tokens")
        cache_miss_tokens = usage_value(usage, "prompt_cache_miss_tokens")
        answer = response.choices[0].message.content.strip() if response.choices else ""
        return DeepSeekInvariantResult(
            label=label,
            request=request,
            check=check,
            answer=answer,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=usage_value(usage, "total_tokens"),
            cost_usd=estimate_request_cost_usd(self.model, prompt_tokens, completion_tokens, cache_hit_tokens, cache_miss_tokens),
            elapsed_seconds=elapsed,
            attempts=attempt,
        )


def client_from_env() -> tuple[OpenAI, str, int]:
    load_dotenv()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    timeout = parse_float(os.getenv("MEMORY_DEEPSEEK_TIMEOUT_SECONDS"), DEFAULT_API_TIMEOUT_SECONDS)
    max_attempts = parse_int(os.getenv("MEMORY_DEEPSEEK_RETRIES"), DEFAULT_API_RETRIES)
    client = OpenAI(api_key=require_env("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL, timeout=timeout, max_retries=0)
    return client, model, max_attempts


def format_result(result: DeepSeekInvariantResult) -> str:
    if result.error:
        return f"- `{result.label}` ERROR attempts={result.attempts}: {result.error}"
    status = "allowed" if result.check.ok else "refused"
    violations = ", ".join(item["invariant_id"] for item in result.check.violations) or "none"
    answer = " ".join(result.answer.split())
    return (
        f"- `{result.label}` status={status}, violations={violations}, "
        f"tokens={result.total_tokens}, attempts={result.attempts}\n"
        f"  DeepSeek: {answer}"
    )


def run_demo(root: Path, report_path: Path) -> str:
    store = InvariantStore(root)
    store.reset()
    checker = InvariantChecker(store.invariants())
    client, model, max_attempts = client_from_env()
    agent = DeepSeekInvariantAgent(client, model, max_attempts)
    scenarios = [
        (
            "allowed",
            "Предложи авторизацию через одноразовый код в текущем Python Telegram-боте: хранить только хеш кода, TTL и попытки, описать этапы planning -> execution -> validation -> done.",
        ),
        (
            "conflict",
            "Сделай авторизацию через Firebase и Node.js, храни коды в открытом виде, без state machine.",
        ),
    ]
    results = []
    for label, request in scenarios:
        store.append_dialog("user", request)
        check = checker.check_request(request)
        store.log_check(label, request, check)
        result = agent.answer(label, request, store.invariants(), check)
        store.append_dialog("assistant", result.answer or result.error)
        results.append(result)
        print(format_result(result), flush=True)

    report = build_report(root, store, model, results)
    report_path.write_text(report, encoding="utf-8")
    print(f"report_saved:{report_path.resolve()}", flush=True)
    return report


def build_report(
    root: Path,
    store: InvariantStore,
    model: str,
    results: list[DeepSeekInvariantResult],
) -> str:
    total_tokens = sum(result.total_tokens for result in results)
    total_cost = sum(result.cost_usd for result in results)
    return "\n".join(
        [
            "# Day 14. Инварианты и ограничения состояния",
            "",
            "## Важно",
            "",
            "- Инварианты хранятся отдельно от диалога.",
            "- Python checker ищет конфликт до ответа модели.",
            "- DeepSeek получает invariant_policy и объясняет отказ.",
            "- Конфликтный запрос не должен пройти.",
            f"- store: `{root}`",
            f"- model: `{model}`, total_tokens={total_tokens}, cost~${total_cost:.6f}",
            "",
            "## Store Files",
            "",
            f"- dialog: `{store.dialog_path}`",
            f"- invariants: `{store.invariants_path}`",
            f"- checks: `{store.checks_path}`",
            "",
            "## Invariants",
            "",
            "```json",
            to_json(store.snapshot()["invariants"]),
            "```",
            "",
            "## DeepSeek API Results",
            "",
            *[format_result(result) for result in results],
            "",
            "## Result",
            "",
            "- Allowed request stays inside invariants.",
            "- Conflict request is refused because it violates stack, security, and state-machine invariants.",
            "- Refusal includes reason and safe alternative.",
        ]
    )


def show_invariants(root: Path) -> str:
    store = InvariantStore(root)
    return to_json(store.snapshot()["invariants"])


def ask(root: Path, request: str) -> str:
    store = InvariantStore(root)
    checker = InvariantChecker(store.invariants())
    client, model, max_attempts = client_from_env()
    agent = DeepSeekInvariantAgent(client, model, max_attempts)
    check = checker.check_request(request)
    store.log_check("manual", request, check)
    return format_result(agent.answer("manual", request, store.invariants(), check))


def main() -> None:
    parser = argparse.ArgumentParser(description="Day 14 invariant-aware assistant with DeepSeek API.")
    parser.add_argument("--store-root", default=os.getenv("DAY14_INVARIANTS_DIR", DEFAULT_STORE_DIR))
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo_parser = subparsers.add_parser("demo", help="run allowed/conflict invariant demo")
    demo_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)

    ask_parser = subparsers.add_parser("ask", help="ask with invariant checking")
    ask_parser.add_argument("request")

    subparsers.add_parser("show-invariants", help="print invariant store")

    args = parser.parse_args()
    root = Path(args.store_root)

    if args.command == "demo":
        run_demo(root, Path(args.report))
        return
    if args.command == "ask":
        print(ask(root, args.request))
        return
    if args.command == "show-invariants":
        print(show_invariants(root))


if __name__ == "__main__":
    main()
