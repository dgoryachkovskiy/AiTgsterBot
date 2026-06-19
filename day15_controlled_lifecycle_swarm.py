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


DEFAULT_STORE_DIR = "day15_lifecycle_store"
DEFAULT_REPORT_PATH = "DAY15_CONTROLLED_LIFECYCLE_REPORT.md"
DEFAULT_GOAL = "добавить авторизацию через одноразовый код в Telegram-боте"

STAGES = ("planning", "execution", "validation", "done")

ALLOWED_TRANSITIONS = {
    "planning": {"approve_plan": "execution", "pause": "planning"},
    "execution": {"finish_execution": "validation", "revise_plan": "planning", "pause": "execution"},
    "validation": {"pass_validation": "done", "fix_needed": "execution", "pause": "validation"},
    "done": {"archive": "done", "pause": "done"},
}

ACTION_PRODUCES = {
    "approve_plan": "approved_plan",
    "finish_execution": "execution_result",
    "pass_validation": "validation_report",
}

STAGE_META = {
    "planning": {
        "current_step": "составить и согласовать план",
        "expected_action": "approve_plan",
        "required_before_enter": [],
        "required_before_leave": [],
    },
    "execution": {
        "current_step": "выполнить утвержденный план",
        "expected_action": "finish_execution",
        "required_before_enter": ["approved_plan"],
        "required_before_leave": ["approved_plan"],
    },
    "validation": {
        "current_step": "проверить результат реализации",
        "expected_action": "pass_validation",
        "required_before_enter": ["approved_plan", "execution_result"],
        "required_before_leave": ["approved_plan", "execution_result"],
    },
    "done": {
        "current_step": "задача завершена",
        "expected_action": "archive",
        "required_before_enter": ["approved_plan", "execution_result", "validation_report"],
        "required_before_leave": [],
    },
}

INVARIANTS = [
    "Нельзя переходить в execution без approve_plan и artifact approved_plan.",
    "Нельзя переходить в validation без finish_execution и artifact execution_result.",
    "Нельзя переходить в done без pass_validation и artifact validation_report.",
    "LLM и рой агентов могут советовать, но финальное решение о переходе принимает TransitionController.",
]

SWARM_AGENTS = [
    {
        "name": "requirements_agent",
        "role": "проверяет цель, ожидаемое действие и полноту требований",
    },
    {
        "name": "architecture_agent",
        "role": "проверяет архитектуру lifecycle, state storage и ограничения",
    },
    {
        "name": "implementation_agent",
        "role": "оценивает, можно ли выполнять следующий практический шаг",
    },
    {
        "name": "qa_agent",
        "role": "ищет ошибки, пропущенную валидацию и риск перепрыгивания этапов",
    },
    {
        "name": "user_value_agent",
        "role": "оценивает, понятен ли результат пользователю и что показать в чате",
    },
]


@dataclass(frozen=True)
class TransitionCheck:
    allowed: bool
    action: str
    from_stage: str
    to_stage: str | None
    reason: str
    missing_artifacts: list[str]


@dataclass(frozen=True)
class ModelCallResult:
    label: str
    answer: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    elapsed_seconds: float
    attempts: int
    error: str = ""


@dataclass(frozen=True)
class SwarmRun:
    stage: str
    action: str
    transition_check: TransitionCheck
    peer_results: list[ModelCallResult]
    orchestrator_result: ModelCallResult
    response_validation: dict[str, Any]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def client_from_env() -> tuple[OpenAI, str, int]:
    load_dotenv()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    timeout = parse_float(os.getenv("MEMORY_DEEPSEEK_TIMEOUT_SECONDS"), DEFAULT_API_TIMEOUT_SECONDS)
    max_attempts = parse_int(os.getenv("MEMORY_DEEPSEEK_RETRIES"), DEFAULT_API_RETRIES)
    client = OpenAI(api_key=require_env("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL, timeout=timeout, max_retries=0)
    return client, model, max_attempts


class LifecycleStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.state_path = self.root / "task_state.json"
        self.rules_path = self.root / "transition_rules.json"
        self.validation_log_path = self.root / "validation_log.json"
        self.swarm_log_path = self.root / "swarm_log.json"
        self.dialog_path = self.root / "dialog.json"
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            self.reset(DEFAULT_GOAL)

    def reset(self, goal: str) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.write_json(
            self.rules_path,
            {
                "allowed_states": list(STAGES),
                "allowed_transitions": ALLOWED_TRANSITIONS,
                "stage_meta": STAGE_META,
                "invariants": INVARIANTS,
            },
        )
        self.write_json(
            self.state_path,
            {
                "task_id": "day15-controlled-lifecycle-demo",
                "goal": goal,
                "stage": "planning",
                "current_step": STAGE_META["planning"]["current_step"],
                "expected_action": STAGE_META["planning"]["expected_action"],
                "paused": False,
                "paused_at": None,
                "resumed_count": 0,
                "artifacts": {},
                "updated_at": now_iso(),
            },
        )
        self.write_json(self.validation_log_path, {"checks": []})
        self.write_json(self.swarm_log_path, {"runs": []})
        self.write_json(self.dialog_path, {"messages": []})

    def read_json(self, path: Path) -> Any:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        tmp.replace(path)

    def read_state(self) -> dict[str, Any]:
        return self.read_json(self.state_path)

    def save_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = now_iso()
        self.write_json(self.state_path, state)

    def read_rules(self) -> dict[str, Any]:
        return self.read_json(self.rules_path)

    def log_validation(self, check: TransitionCheck) -> None:
        log = self.read_json(self.validation_log_path)
        log["checks"].append(
            {
                "time": now_iso(),
                "allowed": check.allowed,
                "action": check.action,
                "from_stage": check.from_stage,
                "to_stage": check.to_stage,
                "reason": check.reason,
                "missing_artifacts": check.missing_artifacts,
            }
        )
        self.write_json(self.validation_log_path, log)

    def log_swarm(self, run: SwarmRun) -> None:
        log = self.read_json(self.swarm_log_path)
        log["runs"].append(
            {
                "time": now_iso(),
                "stage": run.stage,
                "action": run.action,
                "transition_check": run.transition_check.__dict__,
                "peers": [result.__dict__ for result in run.peer_results],
                "orchestrator": run.orchestrator_result.__dict__,
                "response_validation": run.response_validation,
            }
        )
        self.write_json(self.swarm_log_path, log)

    def append_dialog(self, role: str, content: str) -> None:
        dialog = self.read_json(self.dialog_path)
        dialog["messages"].append({"time": now_iso(), "role": role, "content": content})
        self.write_json(self.dialog_path, dialog)

    def pause(self) -> dict[str, Any]:
        state = self.read_state()
        state["paused"] = True
        state["paused_at"] = now_iso()
        self.save_state(state)
        return state

    def resume(self) -> dict[str, Any]:
        state = self.read_state()
        state["paused"] = False
        state["resumed_count"] += 1
        self.save_state(state)
        return state


class TransitionController:
    def __init__(self, store: LifecycleStore) -> None:
        self.store = store

    def validate_state(self, state: dict[str, Any]) -> None:
        stage = state.get("stage")
        if stage not in STAGES:
            raise ValueError(f"Invalid stage: {stage}")
        expected = STAGE_META[stage]["expected_action"]
        if state.get("expected_action") != expected:
            raise ValueError(f"State expected_action must be {expected}, got {state.get('expected_action')}")
        if not isinstance(state.get("artifacts"), dict):
            raise ValueError("State artifacts must be a dict")

    def check_transition(self, action: str) -> TransitionCheck:
        state = self.store.read_state()
        self.validate_state(state)
        stage = state["stage"]
        if action not in ALLOWED_TRANSITIONS[stage]:
            allowed_actions = ", ".join(ALLOWED_TRANSITIONS[stage])
            return TransitionCheck(
                allowed=False,
                action=action,
                from_stage=stage,
                to_stage=None,
                reason=f"action `{action}` is not allowed from `{stage}`; allowed actions: {allowed_actions}",
                missing_artifacts=[],
            )

        to_stage = ALLOWED_TRANSITIONS[stage][action]
        artifacts = state["artifacts"]
        required_current = STAGE_META[stage]["required_before_leave"]
        required_next = STAGE_META[to_stage]["required_before_enter"]
        produced_by_action = ACTION_PRODUCES.get(action)
        required = sorted(name for name in set(required_current + required_next) if name != produced_by_action)
        missing = [name for name in required if name not in artifacts]
        if missing:
            return TransitionCheck(
                allowed=False,
                action=action,
                from_stage=stage,
                to_stage=to_stage,
                reason=f"transition `{stage}` -> `{to_stage}` needs artifacts: {', '.join(missing)}",
                missing_artifacts=missing,
            )

        return TransitionCheck(
            allowed=True,
            action=action,
            from_stage=stage,
            to_stage=to_stage,
            reason="transition allowed by rules and artifact validation",
            missing_artifacts=[],
        )

    def apply_transition(self, check: TransitionCheck, orchestrator_answer: str) -> dict[str, Any]:
        if not check.allowed or check.to_stage is None:
            raise ValueError(check.reason)

        state = self.store.read_state()
        artifact_key, artifact_value = self.artifact_for(check.action, state, orchestrator_answer)
        if artifact_key:
            state["artifacts"][artifact_key] = artifact_value
        state["stage"] = check.to_stage
        state["current_step"] = STAGE_META[check.to_stage]["current_step"]
        state["expected_action"] = STAGE_META[check.to_stage]["expected_action"]
        self.store.save_state(state)
        return state

    def artifact_for(self, action: str, state: dict[str, Any], orchestrator_answer: str) -> tuple[str | None, Any]:
        if action == "approve_plan":
            return (
                "approved_plan",
                {
                    "goal": state["goal"],
                    "steps": [
                        "согласовать требования и ограничения",
                        "реализовать через текущий стек без обхода lifecycle",
                        "проверить результат перед done",
                    ],
                    "orchestrator_summary": orchestrator_answer,
                },
            )
        if action == "finish_execution":
            return (
                "execution_result",
                {
                    "goal": state["goal"],
                    "result": "реализация выполнена по approved_plan",
                    "orchestrator_summary": orchestrator_answer,
                },
            )
        if action == "pass_validation":
            return (
                "validation_report",
                {
                    "checks": [
                        "approved_plan exists",
                        "execution_result exists",
                        "invalid jumps rejected by TransitionController",
                        "pause/resume preserved stage and expected_action",
                    ],
                    "orchestrator_summary": orchestrator_answer,
                },
            )
        return None, None


class DeepSeekCaller:
    def __init__(self, client: OpenAI, model: str, max_attempts: int) -> None:
        self.client = client
        self.model = model
        self.max_attempts = max_attempts

    def call(self, label: str, messages: list[dict[str, str]], max_tokens: int) -> ModelCallResult:
        started = time.perf_counter()
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    extra_body=THINKING_DISABLED,
                    temperature=0,
                    max_tokens=max_tokens,
                    messages=messages,
                )
                break
            except APIStatusError as error:
                elapsed = time.perf_counter() - started
                body = error.response.text[:800] if error.response is not None else ""
                status_code = error.status_code
                should_retry = status_code in {408, 429} or status_code >= 500
                if not should_retry or attempt == self.max_attempts:
                    return ModelCallResult(label, "", 0, 0, 0, 0.0, elapsed, attempt, f"HTTP {status_code}: {body}")
            except (APITimeoutError, APIConnectionError, APIError) as error:
                elapsed = time.perf_counter() - started
                if attempt == self.max_attempts:
                    return ModelCallResult(label, "", 0, 0, 0, 0.0, elapsed, attempt, str(error))
            time.sleep(min(2 * attempt, 6))

        elapsed = time.perf_counter() - started
        usage = response.usage
        prompt_tokens = usage_value(usage, "prompt_tokens")
        completion_tokens = usage_value(usage, "completion_tokens")
        cache_hit_tokens = usage_value(usage, "prompt_cache_hit_tokens")
        cache_miss_tokens = usage_value(usage, "prompt_cache_miss_tokens")
        answer = response.choices[0].message.content.strip() if response.choices else ""
        return ModelCallResult(
            label=label,
            answer=answer,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=usage_value(usage, "total_tokens"),
            cost_usd=estimate_request_cost_usd(self.model, prompt_tokens, completion_tokens, cache_hit_tokens, cache_miss_tokens),
            elapsed_seconds=elapsed,
            attempts=attempt,
        )


class SwarmOrchestrator:
    def __init__(self, caller: DeepSeekCaller, store: LifecycleStore) -> None:
        self.caller = caller
        self.store = store

    def run_swarm(self, check: TransitionCheck) -> SwarmRun:
        state = self.store.read_state()
        rules = self.store.read_rules()
        peer_results: list[ModelCallResult] = []
        for agent in SWARM_AGENTS:
            messages = self.peer_prompt(agent, state, rules, check, peer_results)
            result = self.caller.call(f"{check.from_stage}:{agent['name']}", messages, max_tokens=220)
            peer_results.append(result)

        orchestrator_result = self.caller.call(
            f"{check.from_stage}:orchestrator",
            self.orchestrator_prompt(state, rules, check, peer_results),
            max_tokens=360,
        )
        response_validation = self.validate_orchestrator_response(check, orchestrator_result.answer)
        run = SwarmRun(
            stage=check.from_stage,
            action=check.action,
            transition_check=check,
            peer_results=peer_results,
            orchestrator_result=orchestrator_result,
            response_validation=response_validation,
        )
        self.store.log_swarm(run)
        return run

    def explain_rejection(self, check: TransitionCheck) -> ModelCallResult:
        state = self.store.read_state()
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты оркестратор lifecycle-агента. Кодовый TransitionController уже проверил переход. "
                    "Если allowed=false, объясни отказ и предложи ближайшее разрешенное действие. "
                    "Если allowed=true, объясни почему переход безопасен. Ответь JSON без markdown."
                ),
            },
            {
                "role": "system",
                "content": "state:\n" + to_json(state) + "\ntransition_check:\n" + to_json(check.__dict__),
            },
            {"role": "user", "content": f"Explain rejected action `{check.action}`."},
        ]
        return self.caller.call(f"{check.from_stage}:transition_orchestrator", messages, max_tokens=260)

    def peer_prompt(
        self,
        agent: dict[str, str],
        state: dict[str, Any],
        rules: dict[str, Any],
        check: TransitionCheck,
        peer_results: list[ModelCallResult],
    ) -> list[dict[str, str]]:
        previous = [{"agent": result.label, "answer": result.answer, "error": result.error} for result in peer_results]
        return [
            {
                "role": "system",
                "content": (
                    f"Ты {agent['name']}. Роль: {agent['role']}. "
                    "Ты часть роя из 5 агентов. Оркестратор передает тебе состояние, правила и мнения предыдущих агентов. "
                    "Не меняй state сам. Дай мнение для оркестратора."
                ),
            },
            {
                "role": "system",
                "content": (
                    "state:\n"
                    + to_json(state)
                    + "\nrules:\n"
                    + to_json(rules)
                    + "\ntransition_check:\n"
                    + to_json(check.__dict__)
                    + "\nprevious_peer_opinions:\n"
                    + to_json(previous)
                ),
            },
            {
                "role": "user",
                "content": (
                    "Ответь коротким JSON: agent, vote, concern, recommendation. "
                    "Если переход разрешен кодом, оцени что важно для этого этапа. Если запрещен, поддержи отказ."
                ),
            },
        ]

    def orchestrator_prompt(
        self,
        state: dict[str, Any],
        rules: dict[str, Any],
        check: TransitionCheck,
        peer_results: list[ModelCallResult],
    ) -> list[dict[str, str]]:
        peers = [{"agent": result.label, "answer": result.answer, "error": result.error} for result in peer_results]
        return [
            {
                "role": "system",
                "content": (
                    "Ты главный оркестратор роя агентов. У тебя есть 5 мнений, но источник истины - TransitionController. "
                    "Нельзя разрешать переход, если transition_check.allowed=false. "
                    "Нельзя менять from_stage/to_stage. Ответь JSON без markdown."
                ),
            },
            {
                "role": "system",
                "content": (
                    "state:\n"
                    + to_json(state)
                    + "\nrules:\n"
                    + to_json(rules)
                    + "\ntransition_check:\n"
                    + to_json(check.__dict__)
                    + "\npeer_opinions:\n"
                    + to_json(peers)
                ),
            },
            {
                "role": "user",
                "content": (
                    "Синтезируй итог: status, from_stage, to_stage, action, decision, peer_summary, user_visible_result."
                ),
            },
        ]

    def validate_orchestrator_response(self, check: TransitionCheck, answer: str) -> dict[str, Any]:
        expected_stage = check.to_stage or check.from_stage
        text = answer.lower()
        problems = []
        if check.allowed and expected_stage and expected_stage.lower() not in text:
            problems.append(f"expected to_stage `{expected_stage}` not mentioned")
        if not check.allowed and "refus" not in text and "отказ" not in text and "запрещ" not in text:
            problems.append("rejection answer does not clearly refuse")
        return {"ok": not problems, "expected_stage": expected_stage, "problems": problems}


def format_model_result(result: ModelCallResult) -> str:
    if result.error:
        return f"{result.label}: ERROR attempts={result.attempts} {result.error}"
    compact_answer = " ".join(result.answer.split())
    return f"{result.label}: tokens={result.total_tokens}, attempts={result.attempts}, answer={compact_answer}"


def format_transition_check(check: TransitionCheck) -> str:
    status = "allowed" if check.allowed else "refused"
    to_stage = check.to_stage or "-"
    return f"{check.from_stage} --{check.action}--> {to_stage}: {status}; {check.reason}"


def run_invalid_attempt(
    store: LifecycleStore,
    controller: TransitionController,
    orchestrator: SwarmOrchestrator,
    action: str,
    live: bool,
) -> dict[str, Any]:
    check = controller.check_transition(action)
    store.log_validation(check)
    explanation = orchestrator.explain_rejection(check)
    if live:
        print(f"[invalid] {format_transition_check(check)}", flush=True)
        print(f"  orchestrator: {short_answer(explanation)}", flush=True)
    return {"check": check, "explanation": explanation}


def run_valid_stage(
    store: LifecycleStore,
    controller: TransitionController,
    orchestrator: SwarmOrchestrator,
    action: str,
    live: bool,
) -> SwarmRun:
    check = controller.check_transition(action)
    store.log_validation(check)
    if live:
        print(f"[swarm] {format_transition_check(check)}", flush=True)
        print("  agents: " + ", ".join(agent["name"] for agent in SWARM_AGENTS), flush=True)
    if not check.allowed:
        raise RuntimeError(check.reason)
    run = orchestrator.run_swarm(check)
    next_state = controller.apply_transition(check, run.orchestrator_result.answer)
    if live:
        print(f"  orchestrator: {short_answer(run.orchestrator_result)}", flush=True)
        print(f"  response_validation={run.response_validation['ok']} -> stage={next_state['stage']}", flush=True)
    return run


def short_answer(result: ModelCallResult, limit: int = 220) -> str:
    if result.error:
        return f"ERROR attempts={result.attempts}: {result.error}"
    text = " ".join(result.answer.split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def pause_resume_check(store: LifecycleStore, live: bool) -> dict[str, Any]:
    paused = store.pause()
    reloaded = LifecycleStore(store.root)
    resumed = reloaded.resume()
    ok = (
        paused["stage"] == resumed["stage"]
        and paused["current_step"] == resumed["current_step"]
        and paused["expected_action"] == resumed["expected_action"]
    )
    result = {
        "ok": ok,
        "stage": resumed["stage"],
        "current_step": resumed["current_step"],
        "expected_action": resumed["expected_action"],
        "resumed_count": resumed["resumed_count"],
    }
    if live:
        print(
            f"[pause/resume] ok={ok} stage={resumed['stage']} expected={resumed['expected_action']}",
            flush=True,
        )
    return result


def run_demo(root: Path, report_path: Path, goal: str, live: bool = True) -> str:
    store = LifecycleStore(root)
    store.reset(goal)
    controller = TransitionController(store)
    client, model, max_attempts = client_from_env()
    orchestrator = SwarmOrchestrator(DeepSeekCaller(client, model, max_attempts), store)

    if live:
        print("Day 15 controlled lifecycle demo", flush=True)
        print(f"goal: {goal}", flush=True)
        print(f"flow: {' -> '.join(STAGES)}", flush=True)
        print(f"store: {root}", flush=True)
        print("", flush=True)

    invalid_attempts = []
    swarm_runs: list[SwarmRun] = []
    pause_checks = []

    invalid_attempts.append(run_invalid_attempt(store, controller, orchestrator, "finish_execution", live))
    swarm_runs.append(run_valid_stage(store, controller, orchestrator, "approve_plan", live))
    pause_checks.append(pause_resume_check(store, live))
    print("", flush=True) if live else None

    invalid_attempts.append(run_invalid_attempt(store, controller, orchestrator, "pass_validation", live))
    swarm_runs.append(run_valid_stage(store, controller, orchestrator, "finish_execution", live))
    pause_checks.append(pause_resume_check(store, live))
    print("", flush=True) if live else None

    invalid_attempts.append(run_invalid_attempt(store, controller, orchestrator, "archive", live))
    swarm_runs.append(run_valid_stage(store, controller, orchestrator, "pass_validation", live))
    pause_checks.append(pause_resume_check(store, live))

    final_state = store.read_state()
    report = build_report(root, model, invalid_attempts, swarm_runs, pause_checks, final_state)
    report_path.write_text(report, encoding="utf-8")
    if live:
        print("", flush=True)
        print(f"report_saved:{report_path.resolve()}", flush=True)
    return report


def build_report(
    root: Path,
    model: str,
    invalid_attempts: list[dict[str, Any]],
    swarm_runs: list[SwarmRun],
    pause_checks: list[dict[str, Any]],
    final_state: dict[str, Any],
) -> str:
    all_calls: list[ModelCallResult] = []
    for attempt in invalid_attempts:
        all_calls.append(attempt["explanation"])
    for run in swarm_runs:
        all_calls.extend(run.peer_results)
        all_calls.append(run.orchestrator_result)
    total_tokens = sum(call.total_tokens for call in all_calls)
    total_cost = sum(call.cost_usd for call in all_calls)
    return "\n".join(
        [
            "# Day 15. Контролируемые переходы состояний",
            "",
            "## What Was Built",
            "",
            "- task lifecycle: `planning -> execution -> validation -> done`",
            "- transition rules stored separately in `transition_rules.json`",
            "- task state stored in `task_state.json`",
            "- every transition is validated by Python code before DeepSeek output is trusted",
            "- valid stages use a 5-agent DeepSeek swarm and an orchestrator",
            "- invalid jumps are rejected before state is changed",
            f"- store: `{root}`",
            f"- model: `{model}`, calls={len(all_calls)}, total_tokens={total_tokens}, cost~${total_cost:.6f}",
            "",
            "## Allowed Transitions",
            "",
            "```json",
            to_json(ALLOWED_TRANSITIONS),
            "```",
            "",
            "## Invariants",
            "",
            *[f"- {item}" for item in INVARIANTS],
            "",
            "## Invalid Transition Attempts",
            "",
            *format_invalid_attempts(invalid_attempts),
            "",
            "## Valid Swarm Stages",
            "",
            *format_swarm_runs(swarm_runs),
            "",
            "## Pause / Resume Checks",
            "",
            *[
                f"- ok={item['ok']} stage=`{item['stage']}` expected=`{item['expected_action']}` resumed_count={item['resumed_count']}"
                for item in pause_checks
            ],
            "",
            "## Final State",
            "",
            "```json",
            to_json(final_state),
            "```",
            "",
            "## Result",
            "",
            "- Implementation before plan is rejected.",
            "- Validation/finalization before execution is rejected.",
            "- Done state is reached only after validation.",
            "- Pause/resume reloads saved JSON state and continues from the same stage.",
            "- The swarm gives opinions, but TransitionController remains the hard guardrail.",
        ]
    )


def format_invalid_attempts(invalid_attempts: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for attempt in invalid_attempts:
        check: TransitionCheck = attempt["check"]
        explanation: ModelCallResult = attempt["explanation"]
        lines.extend(
            [
                f"### `{check.action}` from `{check.from_stage}`",
                "",
                f"- code decision: `{format_transition_check(check)}`",
                f"- DeepSeek orchestrator: {short_answer(explanation, limit=600)}",
                "",
            ]
        )
    return lines


def format_swarm_runs(swarm_runs: list[SwarmRun]) -> list[str]:
    lines: list[str] = []
    for run in swarm_runs:
        lines.extend(
            [
                f"### `{run.stage}` action `{run.action}`",
                "",
                f"- transition: `{format_transition_check(run.transition_check)}`",
                f"- response_validation: `{to_json(run.response_validation)}`",
                f"- orchestrator: {short_answer(run.orchestrator_result, limit=700)}",
                "",
                "Peer opinions:",
                "",
            ]
        )
        for result in run.peer_results:
            lines.append(f"- {short_answer(result, limit=400)}")
        lines.append("")
    return lines


def show_state(root: Path) -> str:
    return to_json(LifecycleStore(root).read_state())


def show_rules(root: Path) -> str:
    return to_json(LifecycleStore(root).read_rules())


def try_transition(root: Path, action: str, with_deepseek: bool) -> str:
    store = LifecycleStore(root)
    controller = TransitionController(store)
    check = controller.check_transition(action)
    store.log_validation(check)
    lines = [format_transition_check(check)]
    if with_deepseek:
        client, model, max_attempts = client_from_env()
        explanation = SwarmOrchestrator(DeepSeekCaller(client, model, max_attempts), store).explain_rejection(check)
        lines.append(short_answer(explanation, limit=700))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Day 15 controlled lifecycle with DeepSeek swarm agents.")
    parser.add_argument("--store-root", default=os.getenv("DAY15_LIFECYCLE_DIR", DEFAULT_STORE_DIR))
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo_parser = subparsers.add_parser("demo", help="run controlled lifecycle demo")
    demo_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    demo_parser.add_argument("--goal", default=DEFAULT_GOAL)
    demo_parser.add_argument("--quiet", action="store_true")

    try_parser = subparsers.add_parser("try-transition", help="validate a transition without changing state")
    try_parser.add_argument("action")
    try_parser.add_argument("--with-deepseek", action="store_true")

    subparsers.add_parser("show-state", help="print saved task state")
    subparsers.add_parser("show-rules", help="print lifecycle rules")

    args = parser.parse_args()
    root = Path(args.store_root)

    if args.command == "demo":
        run_demo(root, Path(args.report), args.goal, live=not args.quiet)
        return
    if args.command == "try-transition":
        print(try_transition(root, args.action, args.with_deepseek))
        return
    if args.command == "show-state":
        print(show_state(root))
        return
    if args.command == "show-rules":
        print(show_rules(root))


if __name__ == "__main__":
    main()
