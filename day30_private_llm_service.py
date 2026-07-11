import os
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


DEFAULT_MODEL = "qwen2.5:0.5b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_RATE_LIMIT = 10
DEFAULT_MAX_MESSAGES = 12
DEFAULT_MAX_INPUT_CHARS = 12000
DEFAULT_NUM_CTX = 4096
DEFAULT_NUM_PREDICT = 256
WINDOW_SECONDS = 60
STATIC_DIR = Path(__file__).resolve().parent / "day30_web_static"


app = FastAPI(title="Day30 Private Local LLM Service", version="1.0.0")
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
_rate_windows: dict[str, deque[float]] = defaultdict(deque)


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    model: str | None = None
    temperature: float | None = None
    num_ctx: int | None = None
    num_predict: int | None = None


def env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def settings() -> dict[str, Any]:
    return {
        "api_key": os.getenv("DAY30_API_KEY", ""),
        "model": os.getenv("DAY30_MODEL", DEFAULT_MODEL),
        "ollama_url": os.getenv("DAY30_OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/"),
        "rate_limit_per_minute": env_int("DAY30_RATE_LIMIT_PER_MINUTE", DEFAULT_RATE_LIMIT),
        "max_messages": env_int("DAY30_MAX_MESSAGES", DEFAULT_MAX_MESSAGES),
        "max_input_chars": env_int("DAY30_MAX_INPUT_CHARS", DEFAULT_MAX_INPUT_CHARS),
        "num_ctx": env_int("DAY30_NUM_CTX", DEFAULT_NUM_CTX),
        "num_predict": env_int("DAY30_NUM_PREDICT", DEFAULT_NUM_PREDICT),
    }


def require_auth(authorization: str | None = Header(default=None)) -> str:
    config = settings()
    api_key = config["api_key"]
    if not api_key or api_key == "replace_me":
        raise HTTPException(status_code=500, detail="DAY30_API_KEY is not configured")
    expected = f"Bearer {api_key}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="invalid bearer token")
    return api_key


def check_rate_limit(request: Request, api_key: str) -> dict[str, Any]:
    config = settings()
    limit = int(config["rate_limit_per_minute"])
    client_host = request.client.host if request.client else "unknown"
    key = f"{client_host}:{api_key[-8:]}"
    now = time.monotonic()
    window = _rate_windows[key]
    while window and now - window[0] >= WINDOW_SECONDS:
        window.popleft()
    remaining_before = max(0, limit - len(window))
    if len(window) >= limit:
        retry_after = max(1, int(WINDOW_SECONDS - (now - window[0])))
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "limit_per_minute": limit,
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )
    window.append(now)
    return {
        "limit_per_minute": limit,
        "remaining_before": remaining_before,
        "remaining_after": max(0, limit - len(window)),
        "window_seconds": WINDOW_SECONDS,
    }


def total_chars(messages: list[dict[str, str]]) -> int:
    return sum(len(item.get("content", "")) for item in messages)


def truncate_messages(messages: list[ChatMessage]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    config = settings()
    max_messages = int(config["max_messages"])
    max_chars = int(config["max_input_chars"])
    original = [{"role": item.role, "content": item.content} for item in messages]
    kept = original[:]
    if len(kept) > max_messages:
        system_messages = [item for item in kept if item["role"] == "system"][:1]
        non_system = [item for item in kept if item["role"] != "system"]
        kept = system_messages + non_system[-(max_messages - len(system_messages)) :]
    while len(kept) > 1 and total_chars(kept) > max_chars:
        removable_index = 1 if kept[0]["role"] == "system" else 0
        kept.pop(removable_index)
    if total_chars(kept) > max_chars:
        overflow = total_chars(kept) - max_chars
        kept[-1]["content"] = kept[-1]["content"][overflow:]
    metadata = {
        "messages_before": len(original),
        "messages_sent": len(kept),
        "input_chars_before": total_chars(original),
        "input_chars_sent": total_chars(kept),
        "max_messages": max_messages,
        "max_input_chars": max_chars,
        "truncated": len(original) != len(kept) or total_chars(original) != total_chars(kept),
    }
    return kept, metadata


def bounded_options(request: ChatRequest) -> dict[str, Any]:
    config = settings()
    max_ctx = int(config["num_ctx"])
    max_predict = int(config["num_predict"])
    return {
        "temperature": 0 if request.temperature is None else max(0, min(float(request.temperature), 1)),
        "num_ctx": min(request.num_ctx or max_ctx, max_ctx),
        "num_predict": min(request.num_predict or max_predict, max_predict),
    }


async def ollama_json(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    config = settings()
    async with httpx.AsyncClient(timeout=180) as client:
        if payload is None:
            response = await client.get(f"{config['ollama_url']}{path}")
        else:
            response = await client.post(f"{config['ollama_url']}{path}", json=payload)
        response.raise_for_status()
        return response.json()


@app.get("/", include_in_schema=False)
async def web_chat() -> FileResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="web UI is not installed")
    return FileResponse(index_path)


@app.head("/", include_in_schema=False)
async def web_chat_head() -> Response:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="web UI is not installed")
    return Response(status_code=200, media_type="text/html")


@app.get("/health")
async def health(_: str = Depends(require_auth)) -> dict[str, Any]:
    config = settings()
    try:
        tags = await ollama_json("/api/tags")
        connected = True
        error = ""
    except Exception as exc:
        tags = {}
        connected = False
        error = str(exc)
    return {
        "ok": connected,
        "service": "day30-private-local-llm",
        "ollama_connected": connected,
        "ollama_error": error,
        "model": config["model"],
        "raw_ollama_url": config["ollama_url"],
        "limits": {
            "rate_limit_per_minute": config["rate_limit_per_minute"],
            "max_messages": config["max_messages"],
            "max_input_chars": config["max_input_chars"],
            "num_ctx": config["num_ctx"],
            "num_predict": config["num_predict"],
        },
        "models_count": len(tags.get("models", [])) if isinstance(tags, dict) else 0,
    }


@app.get("/models")
async def models(_: str = Depends(require_auth)) -> dict[str, Any]:
    config = settings()
    tags = await ollama_json("/api/tags")
    model_names = [item.get("name") or item.get("model") for item in tags.get("models", [])]
    metadata: dict[str, Any] = {}
    try:
        metadata = await ollama_json("/api/show", {"model": config["model"]})
    except Exception as exc:
        metadata = {"error": str(exc)}
    return {
        "ok": True,
        "active_model": config["model"],
        "available_models": model_names,
        "active_model_metadata": {
            "details": metadata.get("details"),
            "model_info": metadata.get("model_info"),
        },
    }


@app.post("/chat")
async def chat(request: Request, chat_request: ChatRequest, api_key: str = Depends(require_auth)) -> dict[str, Any]:
    rate = check_rate_limit(request, api_key)
    config = settings()
    requested_model = chat_request.model or config["model"]
    if requested_model != config["model"]:
        raise HTTPException(status_code=400, detail={"error": "model_not_allowed", "allowed_model": config["model"]})
    messages, context = truncate_messages(chat_request.messages)
    started = time.perf_counter()
    response = await ollama_json(
        "/api/chat",
        {
            "model": config["model"],
            "messages": messages,
            "stream": False,
            "options": bounded_options(chat_request),
        },
    )
    elapsed = round(time.perf_counter() - started, 3)
    answer = str(response.get("message", {}).get("content", "")).strip()
    return {
        "ok": bool(answer),
        "model": config["model"],
        "answer": answer,
        "message": response.get("message"),
        "elapsed_seconds": elapsed,
        "usage": {
            "prompt_eval_count": response.get("prompt_eval_count"),
            "eval_count": response.get("eval_count"),
            "total_duration_ns": response.get("total_duration"),
            "eval_duration_ns": response.get("eval_duration"),
        },
        "limits": {
            "rate": rate,
            "context": context,
            "options": bounded_options(chat_request),
        },
    }
