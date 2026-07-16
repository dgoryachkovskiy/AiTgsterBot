import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from day21_document_indexer import load_summary
from day22_rag_agent import CONTROL_QUESTIONS, RetrievedChunk, build_rag_context, retrieve_chunks


DEFAULT_INDEX_DIR = "day21_index_store"
DEFAULT_STORE_DIR = "day29_optimization_store"
DEFAULT_REPORT_PATH = "DAY29_LOCAL_LLM_OPTIMIZATION_REPORT.md"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:0.5b"
DEFAULT_STRATEGY = "structure"
DEFAULT_TOP_K = 5
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_NUM_CTX = 4096
DEFAULT_NUM_PREDICT = 90
DEFAULT_TEMPERATURE = 0.0


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class GenerationProfile:
    name: str
    model: str
    system_prompt: str
    options: dict[str, Any]
    response_format_json: bool = False


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def compact(text: str, limit: int = 500) -> str:
    clean = " ".join(str(text).split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 20].rstrip() + " ...[truncated]"


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
        return float(value)
    except ValueError:
        return default


def load_config() -> None:
    load_dotenv()


def index_dir(args: argparse.Namespace | None = None) -> Path:
    load_config()
    cli_value = getattr(args, "index_dir", None) if args else None
    return Path(cli_value or os.getenv("DAY29_RAG_INDEX_DIR", DEFAULT_INDEX_DIR)).resolve()


def store_dir() -> Path:
    load_config()
    return Path(os.getenv("DAY29_STORE_DIR", DEFAULT_STORE_DIR)).resolve()


def ollama_url() -> str:
    load_config()
    return os.getenv("DAY29_OLLAMA_URL", os.getenv("DAY28_OLLAMA_URL", DEFAULT_OLLAMA_URL)).rstrip("/")


def timeout_seconds() -> int:
    load_config()
    return parse_int(os.getenv("DAY29_TIMEOUT_SECONDS"), DEFAULT_TIMEOUT_SECONDS)


def configured_model(model: str | None) -> str:
    load_config()
    return model or os.getenv("DAY29_LOCAL_MODEL") or DEFAULT_MODEL


def configured_num_ctx(value: int | None = None) -> int:
    return value or parse_int(os.getenv("DAY29_NUM_CTX"), DEFAULT_NUM_CTX)


def configured_num_predict(value: int | None = None) -> int:
    return value or parse_int(os.getenv("DAY29_NUM_PREDICT"), DEFAULT_NUM_PREDICT)


def configured_temperature(value: float | None = None) -> float:
    return DEFAULT_TEMPERATURE if value is None else value


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
    raise RuntimeError("Ollama HTTP API did not start.")


def list_models() -> list[str]:
    data = http_json("/api/tags", timeout=10)
    return [
        str(item.get("name") or item.get("model"))
        for item in data.get("models", [])
        if item.get("name") or item.get("model")
    ]


def ensure_model(model: str) -> None:
    start_ollama_if_needed()
    if model in list_models():
        return
    result = run_command([str(ollama_executable()), "pull", model], timeout=max(timeout_seconds(), 600))
    if result.returncode != 0:
        raise RuntimeError(f"ollama pull failed for {model}: {result.stderr.strip() or result.stdout.strip()}")


def model_metadata(model: str) -> dict[str, str]:
    result = run_command([str(ollama_executable()), "show", model], timeout=30)
    metadata: dict[str, str] = {"model": model, "raw": result.stdout.strip(), "error": result.stderr.strip()}
    if result.returncode != 0:
        return metadata
    patterns = {
        "architecture": r"architecture\s+(.+)",
        "parameters": r"parameters\s+(.+)",
        "context_length": r"context length\s+(.+)",
        "embedding_length": r"embedding length\s+(.+)",
        "quantization": r"quantization\s+(.+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, result.stdout, flags=re.IGNORECASE)
        if match:
            metadata[key] = match.group(1).strip()
    return metadata


def process_snapshot() -> dict[str, Any]:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "Get-Process | Where-Object { $_.ProcessName -like 'ollama*' } | "
            "Measure-Object -Property WorkingSet64,PrivateMemorySize64 -Sum | "
            "Select-Object Property,Count,Sum | ConvertTo-Json -Compress"
        ),
    ]
    try:
        result = run_command(command, timeout=10)
        if result.returncode != 0 or not result.stdout.strip():
            return {"ok": False, "error": result.stderr.strip() or "no process data"}
        parsed = json.loads(result.stdout)
        rows = parsed if isinstance(parsed, list) else [parsed]
        data = {"ok": True, "process_count": 0, "working_set_mb": 0.0, "private_memory_mb": 0.0}
        for row in rows:
            prop = str(row.get("Property", "")).lower()
            data["process_count"] = max(data["process_count"], int(row.get("Count") or 0))
            if prop == "workingset64":
                data["working_set_mb"] = round(float(row.get("Sum") or 0) / 1024 / 1024, 2)
            if prop == "privatememorysize64":
                data["private_memory_mb"] = round(float(row.get("Sum") or 0) / 1024 / 1024, 2)
        return data
    except Exception as error:
        return {"ok": False, "error": str(error)}


def baseline_profile(model: str) -> GenerationProfile:
    return GenerationProfile(
        name="baseline",
        model=model,
        system_prompt=(
            "You are a local-only RAG assistant for the AstroTarot codebase. "
            "Answer using only SOURCES. Cite source IDs like [S1], [S2]. "
            "If sources are insufficient, say that context is insufficient."
        ),
        options={"temperature": 0.1},
    )


def optimized_profile(model: str, num_ctx: int, num_predict: int, temperature: float) -> GenerationProfile:
    return GenerationProfile(
        name="optimized",
        model=model,
        system_prompt=(
            "You are a local-only RAG assistant for the AstroTarot codebase. "
            "Answer in Russian using only SOURCES. "
            "For endpoint questions, include exact HTTP method, path, and request fields. "
            "If sources are insufficient, say that context is insufficient."
        ),
        options={
            "temperature": temperature,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
            "top_p": 0.7,
            "repeat_penalty": 1.05,
        },
    )


def user_prompt(profile: GenerationProfile, question: str, chunks: list[RetrievedChunk]) -> str:
    base = f"QUESTION:\n{question}\n\nSOURCES:\n{build_rag_context(chunks)}"
    if profile.name != "optimized":
        return base
    return (
        f"{base}\n\n"
        "Answer directly in Russian. Keep it concise."
    )


def question_by_id(qid_or_text: str) -> tuple[str, str]:
    for item in CONTROL_QUESTIONS:
        if item.qid == qid_or_text:
            return item.question, item.search_query
    return qid_or_text, qid_or_text


def expected_terms_by_question(question: str) -> list[str]:
    for item in CONTROL_QUESTIONS:
        if item.question == question:
            return list(item.expected_terms)
    return []


def chunk_payload(chunks: list[RetrievedChunk]) -> list[dict[str, Any]]:
    return [
        {
            "rank": chunk.rank,
            "score": round(chunk.score, 6),
            "source": chunk.source,
            "title": chunk.title,
            "section": chunk.section,
            "chunk_id": chunk.chunk_id,
            "preview": compact(chunk.text, 260),
        }
        for chunk in chunks
    ]


def source_ids(chunks: list[RetrievedChunk]) -> list[str]:
    return [f"[S{index}]" for index in range(1, len(chunks) + 1)]


def evaluate_answer(answer: str, elapsed: float, chunks: list[RetrievedChunk], expected_terms: list[str] | None = None) -> dict[str, Any]:
    lowered = answer.lower()
    cites = [source_id for source_id in source_ids(chunks) if source_id.lower() in lowered]
    term_hits = [term for term in (expected_terms or []) if term.lower() in lowered]
    insufficient = "недостаточно" in lowered or "insufficient" in lowered
    ok = bool(answer.strip())
    quality_score = 0
    if ok:
        quality_score += 1
    if cites or term_hits:
        quality_score += 1
    if insufficient or cites or (expected_terms and len(term_hits) >= max(1, len(expected_terms) // 2)):
        quality_score += 1
    if len(answer) <= 1800:
        quality_score += 1
    if expected_terms:
        term_ratio = len(term_hits) / len(expected_terms)
        quality = "ok" if ok and term_ratio >= 0.6 else ("weak" if ok else "fail")
    else:
        term_ratio = None
        quality = "ok" if quality_score >= 3 else ("weak" if ok else "fail")
    return {
        "ok": ok,
        "quality": quality,
        "quality_score": quality_score,
        "has_source_citations": bool(cites),
        "cited_sources": cites,
        "expected_term_hits": term_hits,
        "expected_term_ratio": term_ratio,
        "insufficient_context_detected": insufficient,
        "elapsed_seconds": elapsed,
        "response_chars": len(answer),
        "stable": ok,
    }


def normalize_json_answer(content: str) -> str:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return content
    if not isinstance(parsed, dict):
        return content
    answer = str(parsed.get("answer") or parsed.get("Ответ") or "").strip()
    sources_value = parsed.get("sources") or parsed.get("Источники") or []
    if isinstance(sources_value, list):
        sources = ", ".join(str(item if str(item).startswith("[") else f"[{item}]") for item in sources_value)
    else:
        sources = str(sources_value)
    quality = str(parsed.get("context_quality") or parsed.get("Качество контекста") or "unknown")
    lines = [
        f"Ответ: {answer}" if answer else "Ответ: ",
        f"Источники: {sources}",
        f"Качество контекста: {quality}",
    ]
    return "\n".join(lines)


def generate_answer(
    profile: GenerationProfile,
    question: str,
    chunks: list[RetrievedChunk],
    expected_terms: list[str] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    before = process_snapshot()
    payload = {
        "model": profile.model,
        "messages": [
            {"role": "system", "content": profile.system_prompt},
            {"role": "user", "content": user_prompt(profile, question, chunks)},
        ],
        "stream": False,
        "options": profile.options,
    }
    if profile.response_format_json:
        payload["format"] = "json"
    try:
        data = http_json("/api/chat", payload)
        raw_answer = str(data.get("message", {}).get("content", "")).strip()
        answer = normalize_json_answer(raw_answer) if profile.response_format_json else raw_answer
        elapsed = round(time.perf_counter() - started, 3)
        after = process_snapshot()
        return {
            "profile": profile.name,
            "model": profile.model,
            "options": profile.options,
            "prompt_template": "baseline" if profile.name == "baseline" else "astro_tarot_strict_rag",
            "answer": answer,
            "raw_answer": raw_answer,
            "error": "",
            "ollama_stats": {
                "prompt_eval_count": data.get("prompt_eval_count"),
                "eval_count": data.get("eval_count"),
                "total_duration_ns": data.get("total_duration"),
                "eval_duration_ns": data.get("eval_duration"),
            },
            "resources_before": before,
            "resources_after": after,
            "evaluation": evaluate_answer(answer, elapsed, chunks, expected_terms),
        }
    except Exception as error:
        elapsed = round(time.perf_counter() - started, 3)
        return {
            "profile": profile.name,
            "model": profile.model,
            "options": profile.options,
            "prompt_template": "baseline" if profile.name == "baseline" else "astro_tarot_strict_rag",
            "answer": "",
            "raw_answer": "",
            "error": str(error),
            "ollama_stats": {},
            "resources_before": before,
            "resources_after": process_snapshot(),
            "evaluation": evaluate_answer("", elapsed, chunks, expected_terms),
        }


def run_compare(
    question: str,
    query: str,
    args: argparse.Namespace,
    quant_model: str | None = None,
) -> dict[str, Any]:
    model = configured_model(args.local_model)
    ensure_model(model)
    expected_terms = expected_terms_by_question(question)
    retrieval_started = time.perf_counter()
    chunks = retrieve_chunks(index_dir(args), query or question, args.strategy, args.top_k)
    retrieval_elapsed = round(time.perf_counter() - retrieval_started, 3)
    baseline = generate_answer(baseline_profile(model), question, chunks, expected_terms)
    optimized = generate_answer(
        optimized_profile(model, configured_num_ctx(args.num_ctx), configured_num_predict(args.num_predict), args.temperature),
        question,
        chunks,
        expected_terms,
    )
    quantized = None
    if quant_model:
        ensure_model(quant_model)
        quantized = generate_answer(
            optimized_profile(quant_model, configured_num_ctx(args.num_ctx), configured_num_predict(args.num_predict), args.temperature),
            question,
            chunks,
            expected_terms,
        )
        quantized["profile"] = "quant_model"
    return {
        "question": question,
        "query": query or question,
        "expected_terms": expected_terms,
        "retrieval": {
            "local": True,
            "strategy": args.strategy,
            "top_k": args.top_k,
            "elapsed_seconds": retrieval_elapsed,
            "chunks": chunk_payload(chunks),
        },
        "baseline": baseline,
        "optimized": optimized,
        "quant_model": quantized,
        "winner": choose_winner(baseline, optimized),
    }


def choose_winner(baseline: dict[str, Any], optimized: dict[str, Any]) -> str:
    base_eval = baseline["evaluation"]
    opt_eval = optimized["evaluation"]
    if opt_eval["quality_score"] > base_eval["quality_score"]:
        return "optimized_quality"
    if opt_eval["quality_score"] == base_eval["quality_score"] and opt_eval["elapsed_seconds"] <= base_eval["elapsed_seconds"]:
        return "optimized_speed"
    if opt_eval["quality_score"] == base_eval["quality_score"]:
        return "tie_quality"
    return "baseline_quality"


def build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    def avg_resource(profile: str, field: str) -> float:
        values = [
            float(item[profile]["resources_after"].get(field) or 0)
            for item in results
            if item[profile]["resources_after"].get("ok")
        ]
        return round(sum(values) / len(values), 2) if values else 0.0

    baseline_ok = sum(1 for item in results if item["baseline"]["evaluation"]["ok"])
    optimized_ok = sum(1 for item in results if item["optimized"]["evaluation"]["ok"])
    baseline_quality_ok = sum(1 for item in results if item["baseline"]["evaluation"]["quality"] == "ok")
    optimized_quality_ok = sum(1 for item in results if item["optimized"]["evaluation"]["quality"] == "ok")
    return {
        "questions": len(results),
        "baseline_ok": baseline_ok,
        "optimized_ok": optimized_ok,
        "baseline_quality_ok": baseline_quality_ok,
        "optimized_quality_ok": optimized_quality_ok,
        "avg_retrieval_seconds": round(sum(item["retrieval"]["elapsed_seconds"] for item in results) / len(results), 3),
        "avg_baseline_seconds": round(sum(item["baseline"]["evaluation"]["elapsed_seconds"] for item in results) / len(results), 3),
        "avg_optimized_seconds": round(sum(item["optimized"]["evaluation"]["elapsed_seconds"] for item in results) / len(results), 3),
        "avg_baseline_working_set_mb": avg_resource("baseline", "working_set_mb"),
        "avg_optimized_working_set_mb": avg_resource("optimized", "working_set_mb"),
        "avg_baseline_private_memory_mb": avg_resource("baseline", "private_memory_mb"),
        "avg_optimized_private_memory_mb": avg_resource("optimized", "private_memory_mb"),
        "optimized_wins": sum(1 for item in results if item["winner"].startswith("optimized")),
    }


def build_report(payload: dict[str, Any]) -> str:
    rows = []
    details: list[str] = []
    for item in payload["results"]:
        rows.append(
            "| {question} | {retrieval:.3f} | {bq} | {bs:.3f} | {oq} | {os:.3f} | {winner} |".format(
                question=compact(item["question"], 80).replace("|", "\\|"),
                retrieval=item["retrieval"]["elapsed_seconds"],
                bq=item["baseline"]["evaluation"]["quality"],
                bs=item["baseline"]["evaluation"]["elapsed_seconds"],
                oq=item["optimized"]["evaluation"]["quality"],
                os=item["optimized"]["evaluation"]["elapsed_seconds"],
                winner=item["winner"],
            )
        )
        details.extend(
            [
                f"### {compact(item['question'], 120)}",
                "",
                f"- retrieval_seconds: `{item['retrieval']['elapsed_seconds']}`",
                f"- baseline_quality: `{item['baseline']['evaluation']['quality']}`",
                f"- baseline_seconds: `{item['baseline']['evaluation']['elapsed_seconds']}`",
                f"- baseline_citations: `{item['baseline']['evaluation']['cited_sources']}`",
                f"- baseline_expected_terms: `{item['baseline']['evaluation']['expected_term_hits']}`",
                f"- baseline_working_set_mb: `{item['baseline']['resources_after'].get('working_set_mb', 'unknown')}`",
                f"- baseline_private_memory_mb: `{item['baseline']['resources_after'].get('private_memory_mb', 'unknown')}`",
                f"- optimized_quality: `{item['optimized']['evaluation']['quality']}`",
                f"- optimized_seconds: `{item['optimized']['evaluation']['elapsed_seconds']}`",
                f"- optimized_citations: `{item['optimized']['evaluation']['cited_sources']}`",
                f"- optimized_expected_terms: `{item['optimized']['evaluation']['expected_term_hits']}`",
                f"- optimized_working_set_mb: `{item['optimized']['resources_after'].get('working_set_mb', 'unknown')}`",
                f"- optimized_private_memory_mb: `{item['optimized']['resources_after'].get('private_memory_mb', 'unknown')}`",
                f"- winner: `{item['winner']}`",
                "",
                "**Sources:**",
                "",
                *[
                    f"- [S{idx}] `{chunk['source']}` | `{chunk['section']}` | score `{chunk['score']}`"
                    for idx, chunk in enumerate(item["retrieval"]["chunks"], start=1)
                ],
                "",
                "**Baseline Answer:**",
                "",
                item["baseline"]["answer"] or item["baseline"]["error"],
                "",
                "**Optimized Answer:**",
                "",
                item["optimized"]["answer"] or item["optimized"]["error"],
                "",
            ]
        )
    model = payload["model_metadata"]
    summary = payload["summary"]
    return "\n".join(
        [
            "# Day 29. Local LLM Optimization",
            "",
            "## Summary",
            "",
            f"- generated_at: `{payload['generated_at']}`",
            f"- task: `AstroTarot local RAG`",
            f"- model: `{model.get('model')}`",
            f"- architecture: `{model.get('architecture', 'unknown')}`",
            f"- parameters: `{model.get('parameters', 'unknown')}`",
            f"- context_length: `{model.get('context_length', 'unknown')}`",
            f"- quantization: `{model.get('quantization', 'unknown')}`",
            f"- baseline_options: `{payload['baseline_options']}`",
            f"- optimized_options: `{payload['optimized_options']}`",
            f"- questions: `{summary['questions']}`",
            f"- baseline_quality_ok: `{summary['baseline_quality_ok']}`",
            f"- optimized_quality_ok: `{summary['optimized_quality_ok']}`",
            f"- avg_retrieval_seconds: `{summary['avg_retrieval_seconds']}`",
            f"- avg_baseline_seconds: `{summary['avg_baseline_seconds']}`",
            f"- avg_optimized_seconds: `{summary['avg_optimized_seconds']}`",
            f"- avg_baseline_working_set_mb: `{summary['avg_baseline_working_set_mb']}`",
            f"- avg_optimized_working_set_mb: `{summary['avg_optimized_working_set_mb']}`",
            f"- avg_baseline_private_memory_mb: `{summary['avg_baseline_private_memory_mb']}`",
            f"- avg_optimized_private_memory_mb: `{summary['avg_optimized_private_memory_mb']}`",
            f"- optimized_wins: `{summary['optimized_wins']}`",
            "",
            "## Comparison",
            "",
            "| question | retrieval s | baseline quality | baseline s | optimized quality | optimized s | winner |",
            "|---|---:|---|---:|---|---:|---|",
            *rows,
            "",
            "## Prompt Optimization",
            "",
            "- baseline: simple Day28 RAG prompt, `temperature=0.1`, no explicit context/token limit.",
            "- optimized: AstroTarot-specific RAG prompt for exact endpoint/field extraction and insufficient-context rule.",
            "- optimized options tune `temperature`, `num_ctx`, `num_predict`, `top_p`, `repeat_penalty`.",
            "- quantization is read from real Ollama metadata; installed model reports the active quantized format.",
            "",
            "## Details",
            "",
            *details,
            "## Commands",
            "",
            "```powershell",
            ".\\.venv\\Scripts\\python.exe day29_local_llm_optimizer.py status",
            ".\\.venv\\Scripts\\python.exe day29_local_llm_optimizer.py compare q01",
            ".\\.venv\\Scripts\\python.exe day29_local_llm_optimizer.py verify --limit 5",
            ".\\.venv\\Scripts\\python.exe day29_local_llm_optimizer.py resources",
            "```",
            "",
        ]
    )


def save_outputs(payload: dict[str, Any], report_path: str) -> tuple[Path, Path]:
    target = store_dir()
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "last_verify.json"
    json_path.write_text(to_json(payload), encoding="utf-8")
    report = Path(report_path).resolve()
    report.write_text(build_report(payload), encoding="utf-8")
    return json_path.resolve(), report


def common_args(args: argparse.Namespace) -> argparse.Namespace:
    return args


def command_status(args: argparse.Namespace) -> int:
    start_ollama_if_needed()
    model = configured_model(args.local_model)
    ensure_model(model)
    summary = load_summary(index_dir(args))
    metadata = model_metadata(model)
    print(f"ollama_url={ollama_url()}")
    print(f"models={', '.join(list_models())}")
    print(f"index_dir={index_dir(args)}")
    print(f"embedding_provider={summary['embedding_provider']}")
    print(f"embedding_model={summary['embedding_model']}")
    print(f"local_model={model}")
    print(f"architecture={metadata.get('architecture', 'unknown')}")
    print(f"parameters={metadata.get('parameters', 'unknown')}")
    print(f"context_length={metadata.get('context_length', 'unknown')}")
    print(f"quantization={metadata.get('quantization', 'unknown')}")
    print(f"optimized_options={optimized_profile(model, args.num_ctx, args.num_predict, args.temperature).options}")
    return 0


def command_ask(args: argparse.Namespace) -> int:
    start_ollama_if_needed()
    question, query = question_by_id(args.question)
    model = configured_model(args.local_model)
    ensure_model(model)
    chunks = retrieve_chunks(index_dir(args), query or question, args.strategy, args.top_k)
    profile = baseline_profile(model) if args.profile == "baseline" else optimized_profile(model, args.num_ctx, args.num_predict, args.temperature)
    result = generate_answer(profile, question, chunks, expected_terms_by_question(question))
    print(f"profile={args.profile}")
    print(f"model={model}")
    print(f"quantization={model_metadata(model).get('quantization', 'unknown')}")
    print(f"question={question}")
    for index, chunk in enumerate(chunk_payload(chunks), start=1):
        print(f"[S{index}] {chunk['source']} | {chunk['section']} | score={chunk['score']}")
    print(f"quality={result['evaluation']['quality']} seconds={result['evaluation']['elapsed_seconds']}")
    print("answer:")
    print(result["answer"] or result["error"])
    return 0 if result["evaluation"]["ok"] else 2


def command_compare(args: argparse.Namespace) -> int:
    start_ollama_if_needed()
    question, query = question_by_id(args.question)
    result = run_compare(question, query, common_args(args), args.quant_model)
    print(f"question={question}")
    print(f"retrieval_seconds={result['retrieval']['elapsed_seconds']}")
    print(f"winner={result['winner']}")
    for name in ["baseline", "optimized"]:
        evaluation = result[name]["evaluation"]
        print(
            f"{name}: quality={evaluation['quality']} score={evaluation['quality_score']} "
            f"seconds={evaluation['elapsed_seconds']} citations={evaluation['cited_sources']}"
        )
    if result["quant_model"]:
        evaluation = result["quant_model"]["evaluation"]
        print(
            f"quant_model: model={result['quant_model']['model']} quality={evaluation['quality']} "
            f"seconds={evaluation['elapsed_seconds']} citations={evaluation['cited_sources']}"
        )
    print("baseline_answer:")
    print(result["baseline"]["answer"] or result["baseline"]["error"])
    print("optimized_answer:")
    print(result["optimized"]["answer"] or result["optimized"]["error"])
    return 0 if result["optimized"]["evaluation"]["ok"] else 2


def command_verify(args: argparse.Namespace) -> int:
    start_ollama_if_needed()
    model = configured_model(args.local_model)
    ensure_model(model)
    selected = CONTROL_QUESTIONS[: args.limit]
    results = [run_compare(item.question, item.search_query, common_args(args), args.quant_model) for item in selected]
    payload = {
        "generated_at": now_iso(),
        "model_metadata": model_metadata(model),
        "index_dir": str(index_dir(args)),
        "strategy": args.strategy,
        "top_k": args.top_k,
        "baseline_options": baseline_profile(model).options,
        "optimized_options": optimized_profile(model, args.num_ctx, args.num_predict, args.temperature).options,
        "results": results,
        "summary": build_summary(results),
    }
    json_path, report_path = save_outputs(payload, args.report)
    print(f"model={model}")
    print(f"quantization={payload['model_metadata'].get('quantization', 'unknown')}")
    print(f"baseline_options={payload['baseline_options']}")
    print(f"optimized_options={payload['optimized_options']}")
    for index, item in enumerate(results, start=1):
        print(
            f"q{index} baseline={item['baseline']['evaluation']['quality']} "
            f"{item['baseline']['evaluation']['elapsed_seconds']}s | "
            f"optimized={item['optimized']['evaluation']['quality']} "
            f"{item['optimized']['evaluation']['elapsed_seconds']}s | winner={item['winner']}"
        )
    print(f"summary={to_json(payload['summary'])}")
    print(f"json_saved={json_path}")
    print(f"report_saved={report_path}")
    return 0 if payload["summary"]["optimized_ok"] == len(results) else 2


def command_resources(args: argparse.Namespace) -> int:
    start_ollama_if_needed()
    before = process_snapshot()
    question, query = question_by_id("q01")
    result = run_compare(question, query, common_args(args), args.quant_model)
    after = process_snapshot()
    print(f"before={to_json(before)}")
    print(f"after={to_json(after)}")
    print(f"baseline_resources_after={to_json(result['baseline']['resources_after'])}")
    print(f"optimized_resources_after={to_json(result['optimized']['resources_after'])}")
    print(f"baseline_seconds={result['baseline']['evaluation']['elapsed_seconds']}")
    print(f"optimized_seconds={result['optimized']['evaluation']['elapsed_seconds']}")
    return 0


def add_common(parser: argparse.ArgumentParser) -> None:
    load_config()
    parser.add_argument("--index-dir", default=os.getenv("DAY29_RAG_INDEX_DIR", DEFAULT_INDEX_DIR))
    parser.add_argument("--strategy", default=os.getenv("DAY29_RAG_STRATEGY", DEFAULT_STRATEGY))
    parser.add_argument("--top-k", type=int, default=parse_int(os.getenv("DAY29_TOP_K"), DEFAULT_TOP_K))
    parser.add_argument("--local-model", default=None)
    parser.add_argument("--num-ctx", type=int, default=parse_int(os.getenv("DAY29_NUM_CTX"), DEFAULT_NUM_CTX))
    parser.add_argument("--num-predict", type=int, default=parse_int(os.getenv("DAY29_NUM_PREDICT"), DEFAULT_NUM_PREDICT))
    parser.add_argument("--temperature", type=float, default=parse_float(os.getenv("DAY29_TEMPERATURE"), DEFAULT_TEMPERATURE))
    parser.add_argument("--quant-model", default=os.getenv("DAY29_QUANT_MODEL") or None)


def main() -> None:
    load_config()
    parser = argparse.ArgumentParser(description="Day29 local LLM optimization for AstroTarot RAG.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status")
    add_common(status_parser)
    status_parser.set_defaults(func=command_status)

    ask_parser = subparsers.add_parser("ask")
    ask_parser.add_argument("question")
    ask_parser.add_argument("--profile", choices=["baseline", "optimized"], default="optimized")
    add_common(ask_parser)
    ask_parser.set_defaults(func=command_ask)

    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("question")
    add_common(compare_parser)
    compare_parser.set_defaults(func=command_compare)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--limit", type=int, default=5)
    verify_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    add_common(verify_parser)
    verify_parser.set_defaults(func=command_verify)

    resources_parser = subparsers.add_parser("resources")
    add_common(resources_parser)
    resources_parser.set_defaults(func=command_resources)

    args = parser.parse_args()
    try:
        raise SystemExit(args.func(args))
    except Exception as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
