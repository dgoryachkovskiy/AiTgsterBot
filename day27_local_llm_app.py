import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


DEFAULT_MODEL = "qwen2.5:0.5b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_STORE_DIR = "day27_local_llm_app_store"
DEFAULT_REPORT_PATH = "DAY27_LOCAL_LLM_APP_REPORT.md"
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_RECENT_MESSAGES = 8

DEMO_PROMPTS = [
    "You are a local assistant. Answer in one short paragraph: what is Ollama?",
    "Continue this chat and list 3 reasons why a local LLM can be useful for a private app.",
    "Write a tiny Python add(a, b) function and explain it in one sentence.",
]


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class AppResponse:
    session_id: str
    turn_id: int
    model: str
    prompt: str
    response: str
    elapsed_seconds: float
    ok: bool
    error: str = ""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def load_config() -> None:
    load_dotenv()


def parse_int(value: str | None, default: int) -> int:
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def ollama_url() -> str:
    load_config()
    return os.getenv("DAY27_OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/")


def timeout_seconds() -> int:
    load_config()
    return parse_int(os.getenv("DAY27_TIMEOUT_SECONDS"), DEFAULT_TIMEOUT_SECONDS)


def store_dir() -> Path:
    load_config()
    return Path(os.getenv("DAY27_STORE_DIR", DEFAULT_STORE_DIR)).resolve()


def selected_model(model: str | None) -> str:
    load_config()
    return model or os.getenv("DAY27_LOCAL_MODEL") or DEFAULT_MODEL


def recent_messages_limit(value: int | None = None) -> int:
    load_config()
    return value or parse_int(os.getenv("DAY27_RECENT_MESSAGES"), DEFAULT_RECENT_MESSAGES)


def ollama_executable() -> Path:
    found = shutil.which("ollama")
    if found:
        return Path(found)
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"
        if candidate.exists():
            return candidate
    raise RuntimeError("Ollama CLI not found.")


def http_json(path: str, payload: dict[str, Any] | None = None, timeout: int | None = None) -> dict[str, Any]:
    url = f"{ollama_url()}{path}"
    headers = {}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(request, timeout=timeout or timeout_seconds()) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw.strip() else {}


def api_available() -> tuple[bool, str]:
    try:
        http_json("/api/tags", timeout=5)
        return True, ""
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        return False, str(error)


def start_ollama_if_needed() -> None:
    connected, _ = api_available()
    if connected:
        return
    exe = ollama_executable()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [str(exe), "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        connected, _ = api_available()
        if connected:
            return
        time.sleep(1)
    raise RuntimeError("Ollama HTTP API did not start.")


def list_models() -> list[str]:
    data = http_json("/api/tags", timeout=10)
    return [
        str(item.get("name") or item.get("model"))
        for item in data.get("models", [])
        if item.get("name") or item.get("model")
    ]


def model_installed(model: str) -> bool:
    return model in list_models()


def ensure_model(model: str) -> dict[str, Any]:
    start_ollama_if_needed()
    if model_installed(model):
        return {"model": model, "pulled": False, "message": "model already installed"}
    result = subprocess.run(
        [str(ollama_executable()), "pull", model],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=max(timeout_seconds(), 600),
    )
    return {
        "model": model,
        "pulled": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def safe_session_id(session_id: str) -> str:
    clean = session_id.strip()
    if not clean or any(char in clean for char in "\\/:*?\"<>| "):
        raise ValueError("session_id must be non-empty and file-name safe")
    return clean


def session_dir(session_id: str) -> Path:
    return store_dir() / "sessions" / safe_session_id(session_id)


def messages_path(session_id: str) -> Path:
    return session_dir(session_id) / "messages.json"


def trace_path(session_id: str) -> Path:
    return session_dir(session_id) / "turns.jsonl"


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_json(payload), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_messages(session_id: str) -> list[dict[str, str]]:
    messages = read_json(messages_path(session_id), [])
    return messages if isinstance(messages, list) else []


def save_messages(session_id: str, messages: list[dict[str, str]]) -> None:
    write_json(messages_path(session_id), messages)


def reset_session(session_id: str) -> None:
    root = session_dir(session_id)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    save_messages(session_id, [])
    trace_path(session_id).write_text("", encoding="utf-8")


def local_chat_request(model: str, messages: list[dict[str, str]]) -> tuple[str, float]:
    started = time.perf_counter()
    data = http_json(
        "/api/chat",
        {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.2},
        },
    )
    elapsed = round(time.perf_counter() - started, 3)
    message = data.get("message", {})
    content = str(message.get("content", "")).strip()
    return content, elapsed


def ask_app(session_id: str, prompt: str, model: str, recent_limit: int) -> AppResponse:
    ensure_model(model)
    messages = load_messages(session_id)
    turn_id = sum(1 for item in messages if item.get("role") == "user") + 1
    prompt_messages = [
        {
            "role": "system",
            "content": (
                "You are a local-only CLI assistant. "
                "Use the local Ollama model only. Do not mention cloud APIs unless user asks."
            ),
        },
        *messages[-recent_limit:],
        {"role": "user", "content": prompt},
    ]
    try:
        response, elapsed = local_chat_request(model, prompt_messages)
        ok = bool(response)
        error = ""
    except Exception as exc:
        response = ""
        elapsed = 0.0
        ok = False
        error = str(exc)
    messages.extend(
        [
            {"role": "user", "content": prompt, "created_at": now_iso()},
            {"role": "assistant", "content": response or error, "created_at": now_iso()},
        ]
    )
    save_messages(session_id, messages)
    result = AppResponse(session_id, turn_id, model, prompt, response, elapsed, ok, error)
    append_jsonl(trace_path(session_id), asdict(result))
    return result


def print_response(result: AppResponse) -> None:
    print(f"session_id={result.session_id}")
    print(f"turn_id={result.turn_id}")
    print(f"model={result.model}")
    print("provider=local_ollama")
    print("cloud_models_used=False")
    print(f"ok={result.ok}")
    print(f"elapsed_seconds={result.elapsed_seconds}")
    print("answer:")
    print(result.response or result.error)


def report_text(payload: dict[str, Any]) -> str:
    rows = [
        "| {turn} | {ok} | {elapsed:.3f} | {chars} |".format(
            turn=item["turn_id"],
            ok="yes" if item["ok"] else "no",
            elapsed=float(item["elapsed_seconds"]),
            chars=len(item["response"]),
        )
        for item in payload["turns"]
    ]
    details: list[str] = []
    for item in payload["turns"]:
        details.extend(
            [
                f"### Turn {item['turn_id']}",
                "",
                "**Prompt:**",
                "",
                item["prompt"],
                "",
                "**Answer:**",
                "",
                item["response"] or item.get("error", ""),
                "",
            ]
        )
    return "\n".join(
        [
            "# Day 27. Local LLM Application",
            "",
            "## Summary",
            "",
            f"- generated_at: `{payload['generated_at']}`",
            f"- app_type: `{payload['app_type']}`",
            f"- provider: `{payload['provider']}`",
            f"- local_only: `{payload['local_only']}`",
            f"- cloud_models_used: `{payload['cloud_models_used']}`",
            f"- ollama_url: `{payload['ollama_url']}`",
            f"- model: `{payload['model']}`",
            f"- session_id: `{payload['session_id']}`",
            f"- turns: `{payload['summary']['turns']}`",
            f"- passed: `{payload['summary']['passed']}`",
            "",
            "## Results",
            "",
            "| turn | ok | elapsed seconds | response chars |",
            "|---:|---|---:|---:|",
            *rows,
            "",
            "## Conversation",
            "",
            *details,
            "## Commands",
            "",
            "```powershell",
            ".\\.venv\\Scripts\\python.exe day27_local_llm_app.py status",
            f".\\.venv\\Scripts\\python.exe day27_local_llm_app.py ask \"Hello from local app\" --session-id {payload['session_id']} --model {payload['model']}",
            f".\\.venv\\Scripts\\python.exe day27_local_llm_app.py demo --model {payload['model']}",
            "```",
            "",
        ]
    )


def save_demo_report(payload: dict[str, Any], report_path: str) -> tuple[Path, Path]:
    target = store_dir()
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "last_demo.json"
    json_path.write_text(to_json(payload), encoding="utf-8")
    report = Path(report_path).resolve()
    report.write_text(report_text(payload), encoding="utf-8")
    return json_path.resolve(), report


def command_status(_args: argparse.Namespace) -> int:
    start_ollama_if_needed()
    connected, error = api_available()
    print(f"ollama_path={ollama_executable()}")
    print(f"ollama_url={ollama_url()}")
    print(f"provider=local_ollama")
    print(f"cloud_models_used=False")
    print(f"http_connected={connected}")
    if error:
        print(f"http_error={error}")
    models = list_models() if connected else []
    print(f"models_count={len(models)}")
    for model in models:
        print(f"- {model}")
    return 0 if connected else 2


def command_reset(args: argparse.Namespace) -> int:
    reset_session(args.session_id)
    print(f"reset=True session_id={safe_session_id(args.session_id)}")
    return 0


def command_show(args: argparse.Namespace) -> int:
    messages = load_messages(args.session_id)
    print(f"session_id={safe_session_id(args.session_id)}")
    print(f"messages_count={len(messages)}")
    print(f"messages_path={messages_path(args.session_id).resolve()}")
    print("recent_messages:")
    for item in messages[-args.recent:]:
        print(f"- {item.get('role')}: {str(item.get('content', ''))[:180]}")
    return 0


def command_ask(args: argparse.Namespace) -> int:
    result = ask_app(args.session_id, args.prompt, selected_model(args.model), recent_messages_limit(args.recent))
    print_response(result)
    return 0 if result.ok else 2


def command_chat(args: argparse.Namespace) -> int:
    session_dir(args.session_id).mkdir(parents=True, exist_ok=True)
    model = selected_model(args.model)
    ensure_model(model)
    print(f"session_id={safe_session_id(args.session_id)}")
    print(f"model={model}")
    print("provider=local_ollama")
    print("cloud_models_used=False")
    print("Type /exit to stop.")
    while True:
        try:
            prompt = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if prompt in {"/exit", "/quit"}:
            return 0
        if not prompt:
            continue
        result = ask_app(args.session_id, prompt, model, recent_messages_limit(args.recent))
        print_response(result)


def command_demo(args: argparse.Namespace) -> int:
    model = selected_model(args.model)
    ensure_model(model)
    session_id = args.session_id
    reset_session(session_id)
    turns = [asdict(ask_app(session_id, prompt, model, recent_messages_limit(args.recent))) for prompt in DEMO_PROMPTS]
    payload = {
        "generated_at": now_iso(),
        "app_type": "CLI chat utility",
        "provider": "local_ollama",
        "local_only": True,
        "cloud_models_used": False,
        "ollama_url": ollama_url(),
        "model": model,
        "session_id": session_id,
        "turns": turns,
        "summary": {
            "turns": len(turns),
            "passed": sum(1 for item in turns if item["ok"]),
            "failed": sum(1 for item in turns if not item["ok"]),
        },
    }
    json_path, report_path = save_demo_report(payload, args.report)
    print("local_only=True")
    print("cloud_models_used=False")
    print(f"model={model}")
    for item in turns:
        print(f"turn={item['turn_id']} ok={item['ok']} elapsed={item['elapsed_seconds']}")
        print(item["response"][:400].replace("\n", " "))
    print(f"summary={to_json(payload['summary'])}")
    print(f"json_saved={json_path}")
    print(f"report_saved={report_path}")
    return 0 if payload["summary"]["passed"] == len(DEMO_PROMPTS) else 2


def main() -> None:
    load_config()
    parser = argparse.ArgumentParser(description="Day27 local LLM CLI app. Ollama only; no cloud models.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status")
    status_parser.set_defaults(func=command_status)

    reset_parser = subparsers.add_parser("reset")
    reset_parser.add_argument("--session-id", required=True)
    reset_parser.set_defaults(func=command_reset)

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("--session-id", required=True)
    show_parser.add_argument("--recent", type=int, default=DEFAULT_RECENT_MESSAGES)
    show_parser.set_defaults(func=command_show)

    ask_parser = subparsers.add_parser("ask")
    ask_parser.add_argument("prompt")
    ask_parser.add_argument("--session-id", default="day27_manual")
    ask_parser.add_argument("--model", default=None)
    ask_parser.add_argument("--recent", type=int, default=DEFAULT_RECENT_MESSAGES)
    ask_parser.set_defaults(func=command_ask)

    chat_parser = subparsers.add_parser("chat")
    chat_parser.add_argument("--session-id", default="day27_chat")
    chat_parser.add_argument("--model", default=None)
    chat_parser.add_argument("--recent", type=int, default=DEFAULT_RECENT_MESSAGES)
    chat_parser.set_defaults(func=command_chat)

    demo_parser = subparsers.add_parser("demo")
    demo_parser.add_argument("--session-id", default="day27_demo")
    demo_parser.add_argument("--model", default=None)
    demo_parser.add_argument("--recent", type=int, default=DEFAULT_RECENT_MESSAGES)
    demo_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    demo_parser.set_defaults(func=command_demo)

    args = parser.parse_args()
    try:
        raise SystemExit(args.func(args))
    except Exception as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
