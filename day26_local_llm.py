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
DEFAULT_STORE_DIR = "day26_local_llm_store"
DEFAULT_REPORT_PATH = "DAY26_LOCAL_LLM_REPORT.md"
DEFAULT_TIMEOUT_SECONDS = 180

VERIFY_PROMPTS = [
    {
        "name": "simple_fact",
        "transport": "cli",
        "prompt": "Ответь одним предложением: что такое локальная LLM?",
    },
    {
        "name": "comparison",
        "transport": "http",
        "prompt": "Сравни локальную и облачную LLM в 3 пунктах.",
    },
    {
        "name": "code",
        "transport": "http",
        "prompt": "Напиши короткую Python функцию add(a, b) и объясни ее в 2 предложениях.",
    },
]


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class LocalLlmResult:
    name: str
    transport: str
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


def parse_timeout(value: str | None) -> int:
    if not value:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        parsed = int(value)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return parsed if parsed > 0 else DEFAULT_TIMEOUT_SECONDS


def ollama_url() -> str:
    load_config()
    return os.getenv("DAY26_OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/")


def timeout_seconds() -> int:
    load_config()
    return parse_timeout(os.getenv("DAY26_TIMEOUT_SECONDS"))


def store_dir() -> Path:
    load_config()
    return Path(os.getenv("DAY26_STORE_DIR", DEFAULT_STORE_DIR)).resolve()


def configured_model(model: str | None) -> str | None:
    load_config()
    return model or os.getenv("DAY26_LOCAL_MODEL")


def ollama_executable() -> Path:
    found = shutil.which("ollama")
    if found:
        return Path(found)
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"
        if candidate.exists():
            return candidate
    raise RuntimeError("Ollama CLI not found. Install Ollama or add ollama to PATH.")


def run_command(command: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout or timeout_seconds(),
    )


def http_json(path: str, payload: dict[str, Any] | None = None, timeout: int | None = None) -> dict[str, Any]:
    url = f"{ollama_url()}{path}"
    data = None
    headers = {}
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
    raise RuntimeError("Ollama HTTP API did not become available on localhost:11434.")


def list_models() -> list[str]:
    try:
        data = http_json("/api/tags", timeout=10)
        models = data.get("models", [])
        return [str(item.get("name") or item.get("model")) for item in models if item.get("name") or item.get("model")]
    except Exception:
        return []


def select_model(model: str | None) -> str:
    chosen = configured_model(model)
    if chosen:
        return chosen
    models = list_models()
    for item in models:
        lowered = item.lower()
        if "embed" not in lowered and "nomic" not in lowered:
            return item
    return DEFAULT_MODEL


def model_installed(model: str) -> bool:
    return model in list_models()


def pull_model(model: str) -> dict[str, Any]:
    start_ollama_if_needed()
    if model_installed(model):
        return {"model": model, "pulled": False, "message": "model already installed"}
    exe = ollama_executable()
    started = time.perf_counter()
    result = run_command([str(exe), "pull", model], timeout=max(timeout_seconds(), 600))
    return {
        "model": model,
        "pulled": result.returncode == 0,
        "returncode": result.returncode,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def ask_cli(model: str, prompt: str, name: str = "custom") -> LocalLlmResult:
    exe = ollama_executable()
    started = time.perf_counter()
    result = run_command([str(exe), "run", model, prompt], timeout=timeout_seconds())
    response = result.stdout.strip()
    error = result.stderr.strip()
    ok = result.returncode == 0 and bool(response)
    return LocalLlmResult(name, "cli", prompt, response, round(time.perf_counter() - started, 3), ok, error)


def ask_http(model: str, prompt: str, name: str = "custom") -> LocalLlmResult:
    started = time.perf_counter()
    try:
        data = http_json(
            "/api/generate",
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.2},
            },
        )
        response = str(data.get("response", "")).strip()
        return LocalLlmResult(name, "http", prompt, response, round(time.perf_counter() - started, 3), bool(response))
    except Exception as error:
        return LocalLlmResult(name, "http", prompt, "", round(time.perf_counter() - started, 3), False, str(error))


def ask_model(model: str, prompt: str, transport: str, name: str = "custom") -> LocalLlmResult:
    if transport == "cli":
        return ask_cli(model, prompt, name)
    if transport == "http":
        return ask_http(model, prompt, name)
    raise ValueError(f"Unknown transport: {transport}")


def build_report(payload: dict[str, Any]) -> str:
    rows = []
    details = []
    for item in payload["results"]:
        rows.append(
            "| {name} | {transport} | {ok} | {elapsed:.3f} | {chars} |".format(
                name=item["name"],
                transport=item["transport"],
                ok="yes" if item["ok"] else "no",
                elapsed=float(item["elapsed_seconds"]),
                chars=len(item["response"]),
            )
        )
        details.extend(
            [
                f"### {item['name']}",
                "",
                f"- transport: `{item['transport']}`",
                f"- ok: `{item['ok']}`",
                f"- elapsed_seconds: `{item['elapsed_seconds']}`",
                "",
                "**Prompt:**",
                "",
                item["prompt"],
                "",
                "**Response:**",
                "",
                item["response"] or item.get("error", ""),
                "",
            ]
        )
    return "\n".join(
        [
            "# Day 26. Local LLM with Ollama",
            "",
            "## Summary",
            "",
            f"- generated_at: `{payload['generated_at']}`",
            f"- local_only: `{payload['local_only']}`",
            f"- ollama_path: `{payload['ollama_path']}`",
            f"- ollama_url: `{payload['ollama_url']}`",
            f"- model: `{payload['model']}`",
            f"- http_connected: `{payload['http_connected']}`",
            f"- installed_models: `{', '.join(payload['installed_models'])}`",
            f"- prompts_count: `{len(payload['results'])}`",
            f"- passed: `{payload['summary']['passed']}`",
            "",
            "## Checks",
            "",
            "| prompt | transport | ok | elapsed seconds | response chars |",
            "|---|---|---|---:|---:|",
            *rows,
            "",
            "## Prompt Results",
            "",
            *details,
            "## Commands",
            "",
            "```powershell",
            ".\\.venv\\Scripts\\python.exe day26_local_llm.py status",
            f".\\.venv\\Scripts\\python.exe day26_local_llm.py pull --model {payload['model']}",
            f".\\.venv\\Scripts\\python.exe day26_local_llm.py verify --model {payload['model']}",
            "```",
            "",
        ]
    )


def save_verify(payload: dict[str, Any], report_path: str) -> tuple[Path, Path]:
    target = store_dir()
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "last_verify.json"
    json_path.write_text(to_json(payload), encoding="utf-8")
    report = Path(report_path).resolve()
    report.write_text(build_report(payload), encoding="utf-8")
    return json_path.resolve(), report


def status_payload() -> dict[str, Any]:
    exe = ollama_executable()
    connected, error = api_available()
    return {
        "ollama_path": str(exe),
        "ollama_url": ollama_url(),
        "http_connected": connected,
        "http_error": error,
        "installed_models": list_models() if connected else [],
    }


def command_status(_args: argparse.Namespace) -> int:
    payload = status_payload()
    print(f"ollama_path={payload['ollama_path']}")
    print(f"ollama_url={payload['ollama_url']}")
    print(f"http_connected={payload['http_connected']}")
    if payload["http_error"]:
        print(f"http_error={payload['http_error']}")
    print(f"models_count={len(payload['installed_models'])}")
    for model in payload["installed_models"]:
        print(f"- {model}")
    return 0 if payload["http_connected"] else 2


def command_pull(args: argparse.Namespace) -> int:
    model = select_model(args.model)
    result = pull_model(model)
    print(to_json(result))
    return 0 if result.get("pulled") or result.get("message") == "model already installed" else 2


def command_ask(args: argparse.Namespace) -> int:
    start_ollama_if_needed()
    model = select_model(args.model)
    if not model_installed(model):
        pull_model(model)
    result = ask_model(model, args.prompt, args.transport)
    print(f"model={model}")
    print(f"transport={result.transport}")
    print(f"ok={result.ok}")
    print(f"elapsed_seconds={result.elapsed_seconds}")
    print("response:")
    print(result.response or result.error)
    return 0 if result.ok else 2


def command_verify(args: argparse.Namespace) -> int:
    start_ollama_if_needed()
    model = select_model(args.model)
    pull_info = pull_model(model)
    results = [ask_model(model, item["prompt"], item["transport"], item["name"]) for item in VERIFY_PROMPTS]
    status = status_payload()
    payload = {
        "generated_at": now_iso(),
        "local_only": True,
        "ollama_path": status["ollama_path"],
        "ollama_url": status["ollama_url"],
        "http_connected": status["http_connected"],
        "installed_models": status["installed_models"],
        "model": model,
        "pull": pull_info,
        "results": [asdict(item) for item in results],
        "summary": {
            "passed": sum(1 for item in results if item.ok),
            "failed": sum(1 for item in results if not item.ok),
            "cli_prompts": sum(1 for item in results if item.transport == "cli"),
            "http_prompts": sum(1 for item in results if item.transport == "http"),
        },
    }
    json_path, report_path = save_verify(payload, args.report)
    print(f"local_only=True")
    print(f"ollama_path={payload['ollama_path']}")
    print(f"ollama_url={payload['ollama_url']}")
    print(f"model={model}")
    print(f"http_connected={payload['http_connected']}")
    for item in payload["results"]:
        print(f"{item['name']} transport={item['transport']} ok={item['ok']} elapsed={item['elapsed_seconds']}")
        print(item["response"][:500].replace("\n", " "))
    print(f"summary={to_json(payload['summary'])}")
    print(f"json_saved={json_path}")
    print(f"report_saved={report_path}")
    return 0 if payload["summary"]["passed"] == len(VERIFY_PROMPTS) else 2


def main() -> None:
    load_config()
    parser = argparse.ArgumentParser(description="Day26 local LLM verification through Ollama.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Check local Ollama CLI and HTTP API.")
    status_parser.set_defaults(func=command_status)

    pull_parser = subparsers.add_parser("pull", help="Pull local Ollama model if missing.")
    pull_parser.add_argument("--model", default=None)
    pull_parser.set_defaults(func=command_pull)

    ask_parser = subparsers.add_parser("ask", help="Ask local model one prompt.")
    ask_parser.add_argument("prompt")
    ask_parser.add_argument("--model", default=None)
    ask_parser.add_argument("--transport", choices=["cli", "http"], default="http")
    ask_parser.set_defaults(func=command_ask)

    verify_parser = subparsers.add_parser("verify", help="Run 3 real local LLM prompts and save report.")
    verify_parser.add_argument("--model", default=None)
    verify_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    verify_parser.set_defaults(func=command_verify)

    args = parser.parse_args()
    try:
        raise SystemExit(args.func(args))
    except Exception as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
