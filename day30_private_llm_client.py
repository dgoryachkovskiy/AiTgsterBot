import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


DEFAULT_URL = "http://138.16.168.37:8010"
DEFAULT_STORE_DIR = "day30_private_llm_store"
DEFAULT_REPORT = "DAY30_PRIVATE_LLM_SERVICE_REPORT.md"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def load_config() -> None:
    load_dotenv()


def store_dir() -> Path:
    load_config()
    return Path(os.getenv("DAY30_STORE_DIR", DEFAULT_STORE_DIR)).resolve()


def deploy_info() -> dict[str, Any]:
    path = store_dir() / "deploy.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def service_url(url: str | None = None) -> str:
    load_config()
    return (url or os.getenv("DAY30_SERVICE_URL") or deploy_info().get("service_url") or DEFAULT_URL).rstrip("/")


def api_key(value: str | None = None) -> str:
    load_config()
    key = value or os.getenv("DAY30_API_KEY") or deploy_info().get("api_key") or ""
    if not key or key == "replace_me":
        raise RuntimeError("DAY30_API_KEY missing. Set it in .env or run day30_deploy_vps.py deploy.")
    return key


def request_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    url: str | None,
    key: str | None,
    timeout: int = 180,
) -> tuple[int, dict[str, Any], float]:
    started = time.perf_counter()
    data = None
    headers = {"Authorization": f"Bearer {api_key(key)}"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{service_url(url)}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(raw) if raw.strip() else {}, round(time.perf_counter() - started, 3)
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return error.code, parsed, round(time.perf_counter() - started, 3)


def request_text(method: str, path: str, url: str | None, timeout: int = 30) -> tuple[int, str, float]:
    started = time.perf_counter()
    request = urllib.request.Request(f"{service_url(url)}{path}", method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, raw, round(time.perf_counter() - started, 3)
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        return error.code, raw, round(time.perf_counter() - started, 3)


def chat_payload(prompt: str) -> dict[str, Any]:
    return {
        "messages": [
            {"role": "system", "content": "Ты приватный локальный AI-сервис. Отвечай кратко."},
            {"role": "user", "content": prompt},
        ]
    }


def raw_ollama_check(url: str | None = None) -> dict[str, Any]:
    host = service_url(url).split("//", 1)[-1].split(":", 1)[0]
    raw_url = f"http://{host}:11434/api/tags"
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(raw_url, timeout=5) as response:
            body = response.read().decode("utf-8", errors="replace")
            return {"raw_ollama_url": raw_url, "open": True, "status": response.status, "body_preview": body[:200]}
    except Exception as error:
        return {"raw_ollama_url": raw_url, "open": False, "error": str(error), "elapsed_seconds": round(time.perf_counter() - started, 3)}


def command_health(args: argparse.Namespace) -> int:
    status, data, elapsed = request_json("GET", "/health", None, args.url, args.api_key)
    print(f"status={status} elapsed={elapsed}")
    print(to_json(data))
    return 0 if status == 200 and data.get("ok") else 2


def command_models(args: argparse.Namespace) -> int:
    status, data, elapsed = request_json("GET", "/models", None, args.url, args.api_key)
    print(f"status={status} elapsed={elapsed}")
    print(to_json(data))
    return 0 if status == 200 else 2


def command_chat(args: argparse.Namespace) -> int:
    status, data, elapsed = request_json("POST", "/chat", chat_payload(args.prompt), args.url, args.api_key)
    print(f"status={status} elapsed={elapsed}")
    print(f"model={data.get('model')}")
    print(f"answer={data.get('answer')}")
    print(f"limits={to_json(data.get('limits', {}))}")
    return 0 if status == 200 and data.get("ok") else 2


def run_stability(args: argparse.Namespace) -> list[dict[str, Any]]:
    results = []
    for index in range(1, args.requests + 1):
        status, data, elapsed = request_json(
            "POST",
            "/chat",
            chat_payload(f"Проверка стабильности #{index}: ответь одним коротким предложением."),
            args.url,
            args.api_key,
        )
        results.append({"index": index, "status": status, "ok": data.get("ok", False), "elapsed_seconds": elapsed, "answer": data.get("answer", ""), "data": data})
        print(f"request={index} status={status} ok={data.get('ok', False)} elapsed={elapsed}")
    return results


def command_stability(args: argparse.Namespace) -> int:
    results = run_stability(args)
    passed = sum(1 for item in results if item["status"] == 200 and item["ok"])
    print(f"passed={passed}/{len(results)}")
    return 0 if passed == len(results) else 2


def run_rate_limit(args: argparse.Namespace) -> list[dict[str, Any]]:
    results = []
    got_429 = False
    retry_after = 0
    for index in range(1, args.requests + 1):
        status, data, elapsed = request_json(
            "POST",
            "/chat",
            chat_payload(f"Rate limit probe #{index}. Ответь: ok."),
            args.url,
            args.api_key,
        )
        if status == 429:
            got_429 = True
            detail = data.get("detail", {})
            if isinstance(detail, dict):
                retry_after = max(retry_after, int(detail.get("retry_after_seconds") or 0))
        results.append({"index": index, "status": status, "elapsed_seconds": elapsed, "data": data})
        print(f"request={index} status={status} elapsed={elapsed}")
    if got_429 and args.cooldown:
        wait_seconds = min(max(retry_after + 1, 1), 70)
        print(f"cooldown_wait_seconds={wait_seconds}")
        time.sleep(wait_seconds)
    return results


def command_rate_limit(args: argparse.Namespace) -> int:
    results = run_rate_limit(args)
    got_429 = any(item["status"] == 429 for item in results)
    print(f"rate_limit_triggered={got_429}")
    return 0 if got_429 else 2


def run_max_context(args: argparse.Namespace) -> dict[str, Any]:
    messages = [{"role": "system", "content": "Проверка max context."}]
    for index in range(1, 22):
        messages.append({"role": "user", "content": f"Сообщение {index}: " + ("контекст " * 900)})
        messages.append({"role": "assistant", "content": "Принято."})
    messages.append({"role": "user", "content": "Скажи, был ли контекст ограничен сервисом?"})
    status, data, elapsed = request_json("POST", "/chat", {"messages": messages}, args.url, args.api_key)
    result = {"status": status, "elapsed_seconds": elapsed, "data": data}
    print(f"status={status} elapsed={elapsed}")
    print(f"context_limits={to_json(data.get('limits', {}).get('context', {}))}")
    print(f"answer={data.get('answer')}")
    return result


def command_max_context(args: argparse.Namespace) -> int:
    result = run_max_context(args)
    context = result["data"].get("limits", {}).get("context", {})
    return 0 if result["status"] == 200 and context.get("truncated") else 2


def build_report(results: dict[str, Any]) -> str:
    web = results.get("web", {})
    health = results.get("health", {})
    models = results.get("models", {})
    chat = results.get("chat", {})
    stability = results.get("stability", [])
    rate = results.get("rate_limit", [])
    max_context = results.get("max_context", {})
    raw = results.get("raw_ollama", {})
    stability_ok = sum(1 for item in stability if item["status"] == 200 and item["ok"])
    rate_429 = any(item["status"] == 429 for item in rate)
    context_meta = max_context.get("data", {}).get("limits", {}).get("context", {})
    health_data = health.get("data", {})
    model_data = models.get("data", {})
    model_details = model_data.get("active_model_metadata", {}).get("details", {}) if isinstance(model_data, dict) else {}
    return "\n".join(
        [
            "# Day 30. Private Local LLM HTTP Service",
            "",
            "## Summary",
            "",
            f"- generated_at: `{results['generated_at']}`",
            f"- service_url: `{results['service_url']}`",
            f"- web_ui_status: `{web.get('status')}`",
            f"- web_ui_contains_chat: `{web.get('contains_chat')}`",
            f"- model: `{health_data.get('model', 'unknown')}`",
            f"- quantization: `{model_details.get('quantization_level', 'unknown')}`",
            f"- parameters: `{model_details.get('parameter_size', 'unknown')}`",
            f"- raw_ollama_public: `{raw.get('open')}`",
            f"- auth: `Bearer token`",
            f"- rate_limit_triggered: `{rate_429}`",
            f"- stability_passed: `{stability_ok}/{len(stability)}`",
            f"- max_context_truncated: `{context_meta.get('truncated')}`",
            "",
            "## Web UI",
            "",
            f"- status: `{web.get('status')}`",
            f"- elapsed_seconds: `{web.get('elapsed_seconds')}`",
            f"- contains_title: `{web.get('contains_title')}`",
            f"- contains_chat: `{web.get('contains_chat')}`",
            f"- visual_theme_ru: `Космический AI-наставник`",
            f"- visual_theme: `Космический AI-наставник`",
            f"- browser_entry: `{results['service_url']}/`",
            f"- screenshot_path: `{(store_dir() / 'web_ui_screenshot.png').resolve()}`",
            "",
            "## Health",
            "",
            "```json",
            to_json(health_data),
            "```",
            "",
            "## Models",
            "",
            "```json",
            to_json(model_data),
            "```",
            "",
            "## Chat",
            "",
            f"- status: `{chat.get('status')}`",
            f"- elapsed_seconds: `{chat.get('elapsed_seconds')}`",
            f"- answer: {chat.get('data', {}).get('answer')}",
            "",
            "## Stability",
            "",
            "| request | status | ok | elapsed s |",
            "|---:|---:|---|---:|",
            *[f"| {item['index']} | {item['status']} | {item['ok']} | {item['elapsed_seconds']} |" for item in stability],
            "",
            "## Rate Limit",
            "",
            "| request | status | elapsed s |",
            "|---:|---:|---:|",
            *[f"| {item['index']} | {item['status']} | {item['elapsed_seconds']} |" for item in rate],
            "",
            "## Max Context",
            "",
            "```json",
            to_json(context_meta),
            "```",
            "",
            "## Raw Ollama Exposure",
            "",
            "```json",
            to_json(raw),
            "```",
            "",
        ]
    )


def command_verify(args: argparse.Namespace) -> int:
    store_dir().mkdir(parents=True, exist_ok=True)
    web_status, web_html, web_elapsed = request_text("GET", "/", args.url)
    health_status, health_data, health_elapsed = request_json("GET", "/health", None, args.url, args.api_key)
    models_status, models_data, models_elapsed = request_json("GET", "/models", None, args.url, args.api_key)
    chat_status, chat_data, chat_elapsed = request_json("POST", "/chat", chat_payload("Ответь одним предложением: что такое приватный AI-сервис?"), args.url, args.api_key)
    stability_args = argparse.Namespace(**vars(args))
    stability_args.requests = args.stability_requests
    stability = run_stability(stability_args)
    max_context = run_max_context(args)
    raw = raw_ollama_check(args.url)
    rate_args = argparse.Namespace(**vars(args))
    rate_args.requests = args.rate_requests
    rate_args.cooldown = False
    rate = run_rate_limit(rate_args)
    results = {
        "generated_at": now_iso(),
        "service_url": service_url(args.url),
        "web": {
            "status": web_status,
            "elapsed_seconds": web_elapsed,
            "contains_title": "Космический AI-наставник" in web_html,
            "contains_chat": "chat-panel" in web_html,
            "html_preview": web_html[:400],
        },
        "health": {"status": health_status, "elapsed_seconds": health_elapsed, "data": health_data},
        "models": {"status": models_status, "elapsed_seconds": models_elapsed, "data": models_data},
        "chat": {"status": chat_status, "elapsed_seconds": chat_elapsed, "data": chat_data},
        "stability": stability,
        "max_context": max_context,
        "raw_ollama": raw,
        "rate_limit": rate,
    }
    json_path = store_dir() / "last_verify.json"
    json_path.write_text(to_json(results), encoding="utf-8")
    report_path = Path(args.report).resolve()
    report_path.write_text(build_report(results), encoding="utf-8")
    rate_429 = any(item["status"] == 429 for item in rate)
    stability_ok = sum(1 for item in stability if item["status"] == 200 and item["ok"])
    context_ok = bool(max_context["data"].get("limits", {}).get("context", {}).get("truncated"))
    web_ok = web_status == 200 and "Космический AI-наставник" in web_html and "chat-panel" in web_html
    print(f"web_status={web_status}")
    print(f"web_ui_ok={web_ok}")
    print(f"health_status={health_status}")
    print(f"models_status={models_status}")
    print(f"chat_status={chat_status}")
    print(f"stability_passed={stability_ok}/{len(stability)}")
    print(f"rate_limit_triggered={rate_429}")
    print(f"max_context_truncated={context_ok}")
    print(f"raw_ollama_public={raw.get('open')}")
    print(f"json_saved={json_path.resolve()}")
    print(f"report_saved={report_path}")
    return 0 if web_ok and health_status == 200 and models_status == 200 and chat_status == 200 and stability_ok == len(stability) and rate_429 and context_ok and not raw.get("open") else 2


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", default=None)
    parser.add_argument("--api-key", default=None)


def main() -> None:
    load_config()
    parser = argparse.ArgumentParser(description="Day30 private local LLM service client.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    health_parser = subparsers.add_parser("health")
    add_common(health_parser)
    health_parser.set_defaults(func=command_health)

    models_parser = subparsers.add_parser("models")
    add_common(models_parser)
    models_parser.set_defaults(func=command_models)

    chat_parser = subparsers.add_parser("chat")
    chat_parser.add_argument("prompt")
    add_common(chat_parser)
    chat_parser.set_defaults(func=command_chat)

    stability_parser = subparsers.add_parser("stability")
    stability_parser.add_argument("--requests", type=int, default=5)
    add_common(stability_parser)
    stability_parser.set_defaults(func=command_stability)

    rate_parser = subparsers.add_parser("rate-limit")
    rate_parser.add_argument("--requests", type=int, default=12)
    rate_parser.add_argument("--cooldown", action="store_true", default=True)
    rate_parser.add_argument("--no-cooldown", action="store_false", dest="cooldown")
    add_common(rate_parser)
    rate_parser.set_defaults(func=command_rate_limit)

    max_context_parser = subparsers.add_parser("max-context")
    add_common(max_context_parser)
    max_context_parser.set_defaults(func=command_max_context)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--stability-requests", type=int, default=5)
    verify_parser.add_argument("--rate-requests", type=int, default=12)
    verify_parser.add_argument("--report", default=DEFAULT_REPORT)
    add_common(verify_parser)
    verify_parser.set_defaults(func=command_verify)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
