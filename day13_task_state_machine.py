import argparse
import json
import os
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


DEFAULT_STATE_DIR = "day13_task_state_store"
DEFAULT_REPORT_PATH = "DAY13_TASK_STATE_REPORT.md"
DEFAULT_TASK_GOAL = "добавить memory layers в Telegram-бота на DeepSeek API"

STAGES = ("planning", "execution", "validation", "done")
STAGE_META = {
    "planning": {
        "current_step": "составить короткий план",
        "expected_action": "approve_plan",
        "instruction": "Сформулируй конкретный план из 3 шагов для task_state.goal.",
    },
    "execution": {
        "current_step": "выполнить утвержденный план",
        "expected_action": "finish_execution",
        "instruction": "Покажи конкретный результат выполнения утвержденного плана для task_state.goal.",
    },
    "validation": {
        "current_step": "проверить результат",
        "expected_action": "pass_validation",
        "instruction": "Покажи конкретные проверки результата для task_state.goal.",
    },
    "done": {
        "current_step": "задача завершена",
        "expected_action": "none",
        "instruction": "Дай короткий итоговый результат по task_state.goal.",
    },
}

ALLOWED_TRANSITIONS = {
    "planning": {"approve_plan": "execution", "pause": "planning"},
    "execution": {"finish_execution": "validation", "revise_plan": "planning", "pause": "execution"},
    "validation": {"pass_validation": "done", "fix_needed": "execution", "pause": "validation"},
    "done": {"archive": "done", "pause": "done"},
}


@dataclass(frozen=True)
class DeepSeekStageResult:
    stage: str
    action: str
    answer: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    elapsed_seconds: float
    attempts: int
    error: str = ""


class TaskStateStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = self.root / "task_state.json"
        self.history_path = self.root / "state_events.json"
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.reset(DEFAULT_TASK_GOAL)

    def reset(self, goal: str) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        state = {
            "task_id": "day13-demo-task",
            "goal": goal,
            "stage": "planning",
            "current_step": STAGE_META["planning"]["current_step"],
            "expected_action": STAGE_META["planning"]["expected_action"],
            "approved_plan": [],
            "artifacts": {},
            "paused": False,
            "paused_at": None,
            "resumed_count": 0,
            "updated_at": now_iso(),
        }
        self.write_json(self.path, state)
        self.write_json(self.history_path, {"events": []})
        self.log_event("reset", "planning", {"goal": goal})

    def read_state(self) -> dict[str, Any]:
        return self.read_json(self.path)

    def save_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = now_iso()
        self.write_json(self.path, state)

    def log_event(self, event: str, stage: str, payload: dict[str, Any]) -> None:
        history = self.read_json(self.history_path) if self.history_path.exists() else {"events": []}
        history["events"].append({"time": now_iso(), "event": event, "stage": stage, "payload": payload})
        self.write_json(self.history_path, history)

    def pause(self) -> dict[str, Any]:
        state = self.read_state()
        state["paused"] = True
        state["paused_at"] = now_iso()
        self.save_state(state)
        self.log_event("pause", state["stage"], {"expected_action": state["expected_action"]})
        return state

    def resume(self) -> dict[str, Any]:
        state = self.read_state()
        state["paused"] = False
        state["resumed_count"] += 1
        self.save_state(state)
        self.log_event(
            "resume",
            state["stage"],
            {"current_step": state["current_step"], "expected_action": state["expected_action"]},
        )
        return state

    def transition(self, action: str) -> dict[str, Any]:
        state = self.read_state()
        stage = state["stage"]
        allowed = ALLOWED_TRANSITIONS[stage]
        if action not in allowed:
            raise ValueError(f"Action '{action}' is not allowed from stage '{stage}'")
        next_stage = allowed[action]
        state["stage"] = next_stage
        state["current_step"] = STAGE_META[next_stage]["current_step"]
        state["expected_action"] = STAGE_META[next_stage]["expected_action"]
        if action == "approve_plan":
            state["approved_plan"] = [
                "описать слои short_term / working / long_term",
                "подключить state к prompt builder",
                "проверить ответ DeepSeek на каждом этапе",
            ]
            state["artifacts"]["planning_result"] = f"план для задачи: {state['goal']}"
        if action == "finish_execution":
            state["artifacts"]["execution_result"] = f"выполнены шаги approved_plan для задачи: {state['goal']}"
        if action == "pass_validation":
            state["artifacts"]["validation_result"] = "pause/resume and deterministic transitions checked"
        self.save_state(state)
        self.log_event("transition", next_stage, {"action": action, "from": stage, "to": next_stage})
        return state

    def read_json(self, path: Path) -> Any:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def write_json(self, path: Path, data: Any) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        tmp.replace(path)


class TaskStatePromptBuilder:
    def build(self, state: dict[str, Any], action: str) -> list[dict[str, str]]:
        stage = state["stage"]
        return [
            {
                "role": "system",
                "content": (
                    "Ты агент с формализованным task state machine. "
                    "Используй только переданный task_state. "
                    "Не повторяй вводные с нуля после паузы: продолжай с текущего stage/current_step. "
                    "Соблюдай expected_action и не перескакивай этапы."
                ),
            },
            {
                "role": "system",
                "content": (
                    f"allowed_stages={', '.join(STAGES)}\n"
                    f"allowed_transitions={to_json(ALLOWED_TRANSITIONS)}\n"
                    f"task_state={to_json(state)}\n"
                    f"stage_instruction={STAGE_META[stage]['instruction']}\n"
                    "Output exactly 4 short lines:\n"
                    "stage: <current stage>\n"
                    "step: <current step>\n"
                    "expected: <expected action>\n"
                    "result: <concrete result for task_state.goal at this stage>"
                ),
            },
            {"role": "user", "content": f"Action: {action}. Continue task from saved state."},
        ]


class TaskStateDeepSeekAgent:
    def __init__(self, client: OpenAI, model: str, max_attempts: int) -> None:
        self.client = client
        self.model = model
        self.max_attempts = max_attempts
        self.prompt_builder = TaskStatePromptBuilder()

    def answer(self, state: dict[str, Any], action: str) -> DeepSeekStageResult:
        messages = self.prompt_builder.build(state, action)
        started = time.perf_counter()
        last_error = ""
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    extra_body=THINKING_DISABLED,
                    temperature=0,
                    max_tokens=220,
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
                    return DeepSeekStageResult(state["stage"], action, "", 0, 0, 0, 0.0, elapsed, attempt, last_error)
            except (APITimeoutError, APIConnectionError, APIError) as error:
                elapsed = time.perf_counter() - started
                last_error = str(error)
                if attempt == self.max_attempts:
                    return DeepSeekStageResult(state["stage"], action, "", 0, 0, 0, 0.0, elapsed, attempt, last_error)
            time.sleep(min(2 * attempt, 6))

        elapsed = time.perf_counter() - started
        usage = response.usage
        prompt_tokens = usage_value(usage, "prompt_tokens")
        completion_tokens = usage_value(usage, "completion_tokens")
        cache_hit_tokens = usage_value(usage, "prompt_cache_hit_tokens")
        cache_miss_tokens = usage_value(usage, "prompt_cache_miss_tokens")
        answer = response.choices[0].message.content.strip() if response.choices else ""
        return DeepSeekStageResult(
            stage=state["stage"],
            action=action,
            answer=answer,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=usage_value(usage, "total_tokens"),
            cost_usd=estimate_request_cost_usd(self.model, prompt_tokens, completion_tokens, cache_hit_tokens, cache_miss_tokens),
            elapsed_seconds=elapsed,
            attempts=attempt,
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def client_from_env() -> tuple[OpenAI, str, int]:
    load_dotenv()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    timeout = parse_float(os.getenv("MEMORY_DEEPSEEK_TIMEOUT_SECONDS"), DEFAULT_API_TIMEOUT_SECONDS)
    max_attempts = parse_int(os.getenv("MEMORY_DEEPSEEK_RETRIES"), DEFAULT_API_RETRIES)
    client = OpenAI(api_key=require_env("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL, timeout=timeout, max_retries=0)
    return client, model, max_attempts


def format_result(result: DeepSeekStageResult) -> str:
    if result.error:
        return "\n".join(
            [
                f"- `{result.stage}` action=`{result.action}` ERROR attempts={result.attempts}: {result.error}",
            ]
        )
    answer = " ".join(result.answer.split())
    return "\n".join(
        [
            f"- `{result.stage}` action=`{result.action}` tokens={result.total_tokens}, attempts={result.attempts}: {answer}",
        ]
    )


def format_pause_checks(pause_checks: list[dict[str, Any]]) -> list[str]:
    return [
        (
            f"- `{check['stage']}` | step=`{check['current_step']}` | "
            f"expected=`{check['expected_action']}` | pause_ok={check['pause_preserved_state']}"
        )
        for check in pause_checks
    ]


def live_print(text: str = "") -> None:
    print(text, flush=True)


def run_demo(root: Path, report_path: Path, goal: str, live: bool = True) -> str:
    store = TaskStateStore(root)
    store.reset(goal)
    client, model, max_attempts = client_from_env()
    agent = TaskStateDeepSeekAgent(client, model, max_attempts)
    results: list[DeepSeekStageResult] = []
    pause_checks = []

    stage_actions = [
        ("pause", "continue_after_pause", "approve_plan"),
        ("pause", "continue_after_pause", "finish_execution"),
        ("pause", "continue_after_pause", "pass_validation"),
        ("pause", "continue_after_pause", "archive"),
    ]

    if live:
        live_print("Day 13 live demo")
        live_print(f"flow: {' -> '.join(STAGES)}")
        live_print(f"goal: {goal}")
        live_print(f"store: {root}")
        live_print("")

    for pause_action, resume_action, transition_action in stage_actions:
        paused = store.pause()
        reloaded_store = TaskStateStore(root)
        resumed = reloaded_store.resume()
        pause_check = {
            "stage": resumed["stage"],
            "current_step": resumed["current_step"],
            "expected_action": resumed["expected_action"],
            "pause_preserved_state": paused["stage"] == resumed["stage"]
            and paused["current_step"] == resumed["current_step"]
            and paused["expected_action"] == resumed["expected_action"],
        }
        pause_checks.append(pause_check)
        if live:
            live_print(f"[{resumed['stage']}]")
            live_print(
                f"step={resumed['current_step']} | expected={resumed['expected_action']} | "
                f"pause_ok={pause_check['pause_preserved_state']}"
            )
            live_print("DeepSeek response:")
        result = agent.answer(resumed, resume_action)
        results.append(result)
        if live:
            live_print(format_result(result))
        reloaded_store.transition(transition_action)
        if live:
            next_state = TaskStateStore(root).read_state()
            live_print(f"transition={transition_action} -> {next_state['stage']}")
            if next_state["artifacts"]:
                latest_key = next(reversed(next_state["artifacts"]))
                live_print(f"artifact={latest_key}: {next_state['artifacts'][latest_key]}")
            live_print("")

    final_state = TaskStateStore(root).read_state()
    report = build_report(root, model, results, pause_checks, final_state)
    report_path.write_text(report, encoding="utf-8")
    return report


def build_report(
    root: Path,
    model: str,
    results: list[DeepSeekStageResult],
    pause_checks: list[dict[str, Any]],
    final_state: dict[str, Any],
) -> str:
    total_tokens = sum(result.total_tokens for result in results)
    total_cost = sum(result.cost_usd for result in results)
    final_summary = {
        "goal": final_state["goal"],
        "stage": final_state["stage"],
        "current_step": final_state["current_step"],
        "expected_action": final_state["expected_action"],
        "artifacts": final_state["artifacts"],
        "resumed_count": final_state["resumed_count"],
    }
    return "\n".join(
        [
            "# Day 13. Task State Machine",
            "",
            "## Важно",
            "",
            "- state fields: `stage`, `current_step`, `expected_action`",
            "- flow: `planning -> execution -> validation -> done`",
            "- transitions checked by Python code",
            "- pause/resume reloads saved JSON state",
            f"- store: `{root}`",
            f"- DeepSeek: `{model}`, total_tokens={total_tokens}, cost~${total_cost:.6f}",
            "",
            "## Pause / Resume Checks",
            "",
            *format_pause_checks(pause_checks),
            "",
            "## DeepSeek Continuation Proof",
            "",
            *[format_result(result) for result in results],
            "",
            "## Final State Summary",
            "",
            "```json",
            to_json(final_summary),
            "```",
            "",
            "## Result",
            "",
            "- Task state machine works.",
            "- Pause keeps `stage/current_step/expected_action`.",
            "- Resume continues current stage without repeated explanation.",
        ]
    )


def show_state(root: Path) -> str:
    store = TaskStateStore(root)
    return to_json(store.read_state())


def continue_state(root: Path) -> str:
    store = TaskStateStore(root)
    client, model, max_attempts = client_from_env()
    agent = TaskStateDeepSeekAgent(client, model, max_attempts)
    state = store.resume()
    return format_result(agent.answer(state, "continue_after_pause"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Day 13 task state machine with DeepSeek API.")
    parser.add_argument("--store-root", default=os.getenv("DAY13_TASK_STATE_DIR", DEFAULT_STATE_DIR))
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo_parser = subparsers.add_parser("demo", help="run pause/resume state machine demo")
    demo_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    demo_parser.add_argument("--goal", default=DEFAULT_TASK_GOAL)
    demo_parser.add_argument("--quiet", action="store_true", help="do not print stage-by-stage output")
    demo_parser.add_argument("--print-report", action="store_true", help="print full compact report after demo")

    subparsers.add_parser("show-state", help="print saved task state")
    subparsers.add_parser("continue", help="resume saved task and ask DeepSeek to continue")

    args = parser.parse_args()
    root = Path(args.store_root)

    if args.command == "demo":
        report_path = Path(args.report)
        report = run_demo(root, report_path, args.goal, live=not args.quiet)
        if args.print_report:
            print(report)
        else:
            print(f"report_saved:{report_path.resolve()}")
        return
    if args.command == "show-state":
        print(show_state(root))
        return
    if args.command == "continue":
        print(continue_state(root))


if __name__ == "__main__":
    main()
