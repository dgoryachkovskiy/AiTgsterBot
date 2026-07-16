import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_MEMORY_DIR = "memory_layers_store"
DEFAULT_REPORT_PATH = "MEMORY_LAYERS_REPORT.md"
DEFAULT_API_TIMEOUT_SECONDS = 20.0
DEFAULT_API_RETRIES = 2
THINKING_DISABLED = {"thinking": {"type": "disabled"}}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

MODEL_PRICING_USD_PER_1M = {
    "deepseek-v4-flash": {
        "input_cache_hit": 0.0028,
        "input_cache_miss": 0.14,
        "output": 0.28,
    },
    "deepseek-v4-pro": {
        "input_cache_hit": 0.003625,
        "input_cache_miss": 0.435,
        "output": 0.87,
    },
}

LAYER_NAMES = ("short_term", "working", "long_term")


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def parse_int(value: str | None, default: int) -> int:
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def parse_float(value: str | None, default: float) -> float:
    if not value:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def usage_value(usage: object, name: str) -> int:
    return int(getattr(usage, name, 0) or 0)


def estimate_request_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
) -> float:
    pricing = MODEL_PRICING_USD_PER_1M.get(model, MODEL_PRICING_USD_PER_1M[DEFAULT_MODEL])
    if cache_hit_tokens or cache_miss_tokens:
        input_cost = (
            cache_hit_tokens * pricing["input_cache_hit"] + cache_miss_tokens * pricing["input_cache_miss"]
        ) / 1_000_000
    else:
        input_cost = prompt_tokens * pricing["input_cache_miss"] / 1_000_000
    output_cost = completion_tokens * pricing["output"] / 1_000_000
    return input_cost + output_cost


@dataclass(frozen=True)
class MemoryWrite:
    layer: str
    path: str
    value: Any
    reason: str


@dataclass(frozen=True)
class AgentAnswer:
    label: str
    included_layers: tuple[str, ...]
    prompt_preview: str
    answer: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    elapsed_seconds: float
    attempts: int = 1
    error: str = ""


class MemoryDirectoryStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.paths = {
            "short_term": self.root / "short_term.json",
            "working": self.root / "working.json",
            "long_term": self.root / "long_term.json",
            "write_log": self.root / "write_log.json",
        }
        self.ensure_exists()

    def ensure_exists(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        defaults = self.default_layers()
        for layer, data in defaults.items():
            if not self.paths[layer].exists():
                self.write_json(self.paths[layer], data)

    def reset(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        for layer, data in self.default_layers().items():
            self.write_json(self.paths[layer], data)

    def default_layers(self) -> dict[str, dict[str, Any]]:
        return {
            "short_term": {
                "layer": "short_term",
                "description": "Краткосрочная память: текущий диалог и последние реплики.",
                "messages": [],
            },
            "working": {
                "layer": "working",
                "description": "Рабочая память: данные текущей задачи и состояние процесса.",
                "task": {
                    "stage": None,
                    "goal": None,
                    "constraints": [],
                    "plan": [],
                    "deliverable": None,
                },
                "notes": [],
            },
            "long_term": {
                "layer": "long_term",
                "description": "Долговременная память: профиль, предпочтения, решения и знания.",
                "profile": {},
                "preferences": {},
                "decisions": {},
                "knowledge": {},
            },
            "write_log": {
                "description": "Журнал явного выбора, что и в какой слой памяти сохраняется.",
                "writes": [],
            },
        }

    def read_json(self, path: Path) -> Any:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        tmp.replace(path)

    def load_layer(self, layer: str) -> dict[str, Any]:
        self.validate_layer(layer)
        return self.read_json(self.paths[layer])

    def save_layer(self, layer: str, data: dict[str, Any]) -> None:
        self.validate_layer(layer)
        self.write_json(self.paths[layer], data)

    def validate_layer(self, layer: str) -> None:
        if layer not in LAYER_NAMES:
            raise ValueError(f"Unknown memory layer: {layer}")

    def log_write(self, write: MemoryWrite) -> None:
        log = self.read_json(self.paths["write_log"])
        log["writes"].append(
            {
                "time": now_iso(),
                "layer": write.layer,
                "path": write.path,
                "reason": write.reason,
                "value": write.value,
            }
        )
        self.write_json(self.paths["write_log"], log)

    def save_short_term(self, role: str, content: str, reason: str) -> None:
        data = self.load_layer("short_term")
        value = {"role": role, "content": content, "saved_at": now_iso()}
        data["messages"].append(value)
        self.save_layer("short_term", data)
        self.log_write(MemoryWrite("short_term", "messages[]", value, reason))

    def save_working(self, key: str, value: Any, reason: str) -> None:
        data = self.load_layer("working")
        if key == "notes":
            data["notes"].append(value)
            path = "notes[]"
        else:
            data["task"][key] = value
            path = f"task.{key}"
        self.save_layer("working", data)
        self.log_write(MemoryWrite("working", path, value, reason))

    def save_long_term(self, section: str, key: str, value: Any, reason: str) -> None:
        if section not in {"profile", "preferences", "decisions", "knowledge"}:
            raise ValueError("long_term section must be profile, preferences, decisions, or knowledge")
        data = self.load_layer("long_term")
        data[section][key] = value
        self.save_layer("long_term", data)
        self.log_write(MemoryWrite("long_term", f"{section}.{key}", value, reason))

    def view(self, layers: Iterable[str]) -> dict[str, Any]:
        return {layer: self.load_layer(layer) for layer in layers}

    def write_log(self) -> dict[str, Any]:
        return self.read_json(self.paths["write_log"])


class PromptBuilder:
    def build(self, user_request: str, memory: dict[str, Any], included_layers: tuple[str, ...]) -> list[dict[str, str]]:
        selected = ", ".join(included_layers)
        return [
            {
                "role": "system",
                "content": (
                    "Ты stateful-ассистент с явными слоями памяти. "
                    "Используй только переданный JSON memory_layers. "
                    "Не выдумывай отсутствующие факты: если данных нет в выбранных слоях, ставь null. "
                    "Отвечай коротким JSON без markdown."
                ),
            },
            {
                "role": "system",
                "content": f"selected_memory_layers={selected}\nmemory_layers:\n{to_json(memory)}",
            },
            {"role": "user", "content": user_request},
        ]


class DeepSeekMemoryAgent:
    def __init__(self, client: OpenAI, model: str, store: MemoryDirectoryStore, max_attempts: int) -> None:
        self.client = client
        self.model = model
        self.store = store
        self.max_attempts = max_attempts
        self.prompt_builder = PromptBuilder()

    def answer(self, user_request: str, included_layers: tuple[str, ...], label: str) -> AgentAnswer:
        memory = self.store.view(included_layers)
        messages = self.prompt_builder.build(user_request, memory, included_layers)
        prompt_preview = messages[1]["content"]
        started = time.perf_counter()
        last_error = ""
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    extra_body=THINKING_DISABLED,
                    temperature=0,
                    max_tokens=420,
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
                    return AgentAnswer(label, included_layers, prompt_preview, "", 0, 0, 0, 0.0, elapsed, attempt, last_error)
            except (APITimeoutError, APIConnectionError, APIError) as error:
                elapsed = time.perf_counter() - started
                last_error = str(error)
                if attempt == self.max_attempts:
                    return AgentAnswer(label, included_layers, prompt_preview, "", 0, 0, 0, 0.0, elapsed, attempt, last_error)
            time.sleep(min(2 * attempt, 6))

        elapsed = time.perf_counter() - started
        usage = response.usage
        prompt_tokens = usage_value(usage, "prompt_tokens")
        completion_tokens = usage_value(usage, "completion_tokens")
        cache_hit_tokens = usage_value(usage, "prompt_cache_hit_tokens")
        cache_miss_tokens = usage_value(usage, "prompt_cache_miss_tokens")
        answer = response.choices[0].message.content.strip() if response.choices else ""
        return AgentAnswer(
            label=label,
            included_layers=included_layers,
            prompt_preview=prompt_preview,
            answer=answer,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=usage_value(usage, "total_tokens"),
            cost_usd=estimate_request_cost_usd(
                self.model,
                prompt_tokens,
                completion_tokens,
                cache_hit_tokens,
                cache_miss_tokens,
            ),
            elapsed_seconds=elapsed,
            attempts=attempt,
        )


def parse_layers(raw: str) -> tuple[str, ...]:
    if raw.strip().lower() == "all":
        return LAYER_NAMES
    layers = tuple(part.strip() for part in raw.split(",") if part.strip())
    invalid = [layer for layer in layers if layer not in LAYER_NAMES]
    if invalid:
        raise ValueError(f"Invalid layers: {', '.join(invalid)}")
    if not layers:
        raise ValueError("At least one layer is required")
    return layers


def create_agent(store: MemoryDirectoryStore) -> DeepSeekMemoryAgent:
    load_dotenv()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    timeout = parse_float(os.getenv("MEMORY_DEEPSEEK_TIMEOUT_SECONDS"), DEFAULT_API_TIMEOUT_SECONDS)
    max_attempts = parse_int(os.getenv("MEMORY_DEEPSEEK_RETRIES"), DEFAULT_API_RETRIES)
    client = OpenAI(api_key=require_env("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL, timeout=timeout, max_retries=0)
    return DeepSeekMemoryAgent(client, model, store, max_attempts=max_attempts)


def seed_demo_memory(store: MemoryDirectoryStore) -> None:
    store.reset()
    store.save_short_term(
        "user",
        "Нужно сделать ассистента с явной моделью памяти.",
        "краткосрочная память: текущая реплика пользователя",
    )
    store.save_short_term(
        "assistant",
        "Разделю память на short_term, working и long_term.",
        "краткосрочная память: ответ ассистента в текущем диалоге",
    )
    store.save_short_term(
        "user",
        "Проверь через DeepSeek API, как выбранные слои влияют на ответ.",
        "краткосрочная память: актуальный запрос в текущей сессии",
    )

    store.save_working("stage", "planning", "рабочая память: текущий этап задачи")
    store.save_working(
        "goal",
        "описать и реализовать модель памяти для ассистента",
        "рабочая память: цель активной задачи",
    )
    store.save_working(
        "constraints",
        [
            "минимум 3 типа памяти",
            "разные типы памяти хранятся отдельно",
            "выбор слоя для сохранения должен быть явным",
            "использовать реальные вызовы DeepSeek API",
            "не трогать Telegram-бота",
        ],
        "рабочая память: ограничения текущей задачи",
    )
    store.save_working(
        "plan",
        [
            "создать файловое хранилище слоев",
            "реализовать prompt builder с выбором слоев",
            "сравнить ответы DeepSeek при разных наборах памяти",
            "записать markdown-отчет и mp4-демо",
        ],
        "рабочая память: план выполнения текущей задачи",
    )
    store.save_working(
        "deliverable",
        "CLI-агент, JSON memory store, DeepSeek API отчет, видео демонстрация",
        "рабочая память: ожидаемый результат",
    )
    store.save_working(
        "notes",
        "Один и тот же вопрос будет задан с all_layers, without_working, without_long_term и short_term_only.",
        "рабочая память: метод проверки влияния слоев",
    )

    store.save_long_term("profile", "name", "Данил", "долговременная память: профиль пользователя")
    store.save_long_term(
        "preferences",
        "answer_style",
        "коротко и по делу, без лишней теории",
        "долговременная память: стабильное предпочтение формата ответа",
    )
    store.save_long_term(
        "decisions",
        "llm_provider",
        "использовать DeepSeek API для практических проверок",
        "долговременная память: решение применимо к будущим заданиям",
    )
    store.save_long_term(
        "decisions",
        "memory_policy",
        "профиль и стабильные решения хранить в long_term, детали активной задачи в working",
        "долговременная память: политика сохранения памяти",
    )
    store.save_long_term(
        "knowledge",
        "project",
        "AiTgsterBot - Telegram-ассистент, который использует DeepSeek API.",
        "долговременная память: знание о проекте",
    )


def demo_question() -> str:
    return (
        "Кто пользователь, какой стиль ответа он предпочитает, какая текущая задача, "
        "какая стадия, ограничения, deliverable, стабильные решения и знание о проекте? "
        "Верни JSON с ключами user, preferences, current_task, stage, constraints, deliverable, decisions, project."
    )


def run_demo(store_dir: Path, report_path: Path) -> str:
    store = MemoryDirectoryStore(store_dir)
    seed_demo_memory(store)
    agent = create_agent(store)
    question = demo_question()
    scenarios = [
        ("all_layers", ("short_term", "working", "long_term")),
        ("without_working", ("short_term", "long_term")),
        ("without_long_term", ("short_term", "working")),
        ("short_term_only", ("short_term",)),
    ]
    answers = [agent.answer(question, layers, label) for label, layers in scenarios]
    report = build_report(store, agent.model, question, answers)
    report_path.write_text(report, encoding="utf-8")
    return report


def run_ask(store_dir: Path, question: str, layers: tuple[str, ...]) -> str:
    store = MemoryDirectoryStore(store_dir)
    agent = create_agent(store)
    answer = agent.answer(question, layers, "manual_ask")
    return format_answer(answer)


def format_answer(answer: AgentAnswer) -> str:
    if answer.error:
        return "\n".join(
            [
                f"### {answer.label}",
                "",
                f"layers={', '.join(answer.included_layers)}",
                f"ERROR after {answer.elapsed_seconds:.1f}s",
                f"attempts={answer.attempts}",
                "",
                answer.error,
                "",
            ]
        )
    return "\n".join(
        [
            f"### {answer.label}",
            "",
            f"layers={', '.join(answer.included_layers)}",
            f"prompt={answer.prompt_tokens}, answer={answer.completion_tokens}, total={answer.total_tokens}",
            f"cost~${answer.cost_usd:.6f}, time={answer.elapsed_seconds:.1f}s, attempts={answer.attempts}",
            "",
            "```json",
            answer.answer,
            "```",
            "",
        ]
    )


def build_report(store: MemoryDirectoryStore, model: str, question: str, answers: list[AgentAnswer]) -> str:
    snapshots = store.view(LAYER_NAMES)
    write_log = store.write_log()["writes"]
    write_lines = [
        f"- `{entry['layer']}` -> `{entry['path']}`: {entry['reason']}"
        for entry in write_log
    ]
    prompt_lines = [
        f"- `{answer.label}` selected layers: `{', '.join(answer.included_layers)}`"
        for answer in answers
    ]
    total_cost = sum(answer.cost_usd for answer in answers)
    total_tokens = sum(answer.total_tokens for answer in answers)
    return "\n".join(
        [
            "# Memory Layers Agent",
            "",
            "## Модель памяти",
            "",
            "- `short_term`: краткосрочная память текущего диалога.",
            "- `working`: рабочая память текущей задачи: стадия, цель, ограничения, план, deliverable.",
            "- `long_term`: долговременная память: профиль, предпочтения, решения, знания.",
            "",
            "Данные сохраняются явно: для каждого слоя есть отдельный метод записи и отдельный JSON-файл.",
            "Prompt builder получает список слоев и подмешивает только выбранную память, а не весь store.",
            "",
            "## Файлы памяти",
            "",
            f"- `{store.paths['short_term']}`",
            f"- `{store.paths['working']}`",
            f"- `{store.paths['long_term']}`",
            f"- `{store.paths['write_log']}`",
            "",
            "## Explicit Write Log",
            "",
            *write_lines,
            "",
            "## Memory Snapshot",
            "",
            "```json",
            to_json(snapshots),
            "```",
            "",
            "## Prompt Layer Selection",
            "",
            *prompt_lines,
            "",
            "## DeepSeek API Check",
            "",
            f"model={model}",
            f"question={question}",
            f"total_tokens={total_tokens}",
            f"total_cost~${total_cost:.6f}",
            "",
            *[format_answer(answer) for answer in answers],
            "## Вывод",
            "",
            "- `all_layers`: ассистент видит текущий диалог, задачу, профиль, решения и знания.",
            "- `without_working`: ассистент сохраняет профиль и проект, но теряет цель, стадию, ограничения и deliverable текущей задачи.",
            "- `without_long_term`: ассистент понимает текущую задачу, но теряет профиль пользователя и стабильные решения.",
            "- `short_term_only`: ассистент видит только последние реплики и не знает ни рабочее состояние, ни долговременную память.",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone DeepSeek agent with explicit memory layers.")
    parser.add_argument("--store-dir", default=os.getenv("MEMORY_LAYERS_DIR", DEFAULT_MEMORY_DIR))
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo_parser = subparsers.add_parser("demo", help="seed memory, call DeepSeek, write report")
    demo_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)

    show_parser = subparsers.add_parser("show", help="print selected memory layers")
    show_parser.add_argument("--layers", default="all", help="all or comma list: short_term,working,long_term")

    ask_parser = subparsers.add_parser("ask", help="ask DeepSeek using selected memory layers")
    ask_parser.add_argument("question")
    ask_parser.add_argument("--layers", default="all", help="all or comma list: short_term,working,long_term")

    subparsers.add_parser("reset", help="clear memory layer store")

    args = parser.parse_args()
    store_dir = Path(args.store_dir)

    if args.command == "demo":
        print(run_demo(store_dir, Path(args.report)))
        return

    if args.command == "show":
        store = MemoryDirectoryStore(store_dir)
        print(to_json(store.view(parse_layers(args.layers))))
        return

    if args.command == "ask":
        print(run_ask(store_dir, args.question, parse_layers(args.layers)))
        return

    if args.command == "reset":
        MemoryDirectoryStore(store_dir).reset()
        print(f"reset:{store_dir}")


if __name__ == "__main__":
    main()
