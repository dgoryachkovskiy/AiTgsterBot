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

from day21_document_indexer import load_summary
from day22_rag_agent import CONTROL_QUESTIONS, RetrievedChunk, build_rag_context, retrieve_chunks


DEFAULT_INDEX_DIR = "day21_index_store"
DEFAULT_STORE_DIR = "day28_local_rag_store"
DEFAULT_REPORT_PATH = "DAY28_LOCAL_RAG_REPORT.md"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_LOCAL_MODEL = "qwen2.5:0.5b"
DEFAULT_STRATEGY = "structure"
DEFAULT_TOP_K = 5
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_CLOUD_MODEL = "deepseek-v4-flash"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
THINKING_DISABLED = {"thinking": {"type": "disabled"}}


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class RagAnswer:
    provider: str
    model: str
    answer: str
    elapsed_seconds: float
    ok: bool
    error: str = ""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def compact(text: str, limit: int = 1200) -> str:
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


def load_config() -> None:
    load_dotenv()


def index_dir(args: argparse.Namespace | None = None) -> Path:
    load_config()
    cli_value = getattr(args, "index_dir", None) if args else None
    return Path(cli_value or os.getenv("DAY28_RAG_INDEX_DIR", DEFAULT_INDEX_DIR)).resolve()


def store_dir() -> Path:
    load_config()
    return Path(os.getenv("DAY28_STORE_DIR", DEFAULT_STORE_DIR)).resolve()


def ollama_url() -> str:
    load_config()
    return os.getenv("DAY28_OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/")


def local_model(model: str | None = None) -> str:
    load_config()
    return model or os.getenv("DAY28_LOCAL_MODEL") or DEFAULT_LOCAL_MODEL


def timeout_seconds() -> int:
    load_config()
    return parse_int(os.getenv("DAY28_TIMEOUT_SECONDS"), DEFAULT_TIMEOUT_SECONDS)


def cloud_model() -> str:
    load_config()
    return os.getenv("DAY28_CLOUD_MODEL") or os.getenv("DEEPSEEK_MODEL") or DEFAULT_CLOUD_MODEL


def cloud_available() -> bool:
    load_config()
    return bool(os.getenv("DEEPSEEK_API_KEY"))


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


def ensure_model(model: str) -> dict[str, Any]:
    start_ollama_if_needed()
    if model in list_models():
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


def local_rag_answer(question: str, chunks: list[RetrievedChunk], model: str) -> RagAnswer:
    started = time.perf_counter()
    messages = [
        {
            "role": "system",
            "content": (
                "You are a local-only RAG assistant for the AstroTarot codebase. "
                "Answer using only SOURCES. Cite source IDs like [S1], [S2]. "
                "If sources are insufficient, say that context is insufficient. "
                "No cloud model is available in this mode."
            ),
        },
        {
            "role": "user",
            "content": f"QUESTION:\n{question}\n\nSOURCES:\n{build_rag_context(chunks)}",
        },
    ]
    try:
        data = http_json(
            "/api/chat",
            {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.1},
            },
        )
        content = str(data.get("message", {}).get("content", "")).strip()
        return RagAnswer("local_ollama_rag", model, content, round(time.perf_counter() - started, 3), bool(content))
    except Exception as error:
        return RagAnswer("local_ollama_rag", model, "", round(time.perf_counter() - started, 3), False, str(error))


def cloud_rag_answer(question: str, chunks: list[RetrievedChunk]) -> RagAnswer:
    if not cloud_available():
        return RagAnswer("cloud_deepseek_rag", cloud_model(), "", 0.0, False, "DEEPSEEK_API_KEY is not set")
    started = time.perf_counter()
    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL, timeout=timeout_seconds(), max_retries=0)
        response = client.chat.completions.create(
            model=cloud_model(),
            extra_body=THINKING_DISABLED,
            temperature=0,
            max_tokens=700,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a cloud RAG assistant for AstroTarot. Use only SOURCES. "
                        "Cite source IDs like [S1], [S2]. Be concise."
                    ),
                },
                {
                    "role": "user",
                    "content": f"QUESTION:\n{question}\n\nSOURCES:\n{build_rag_context(chunks)}",
                },
            ],
        )
        answer = response.choices[0].message.content.strip() if response.choices else ""
        return RagAnswer("cloud_deepseek_rag", cloud_model(), answer, round(time.perf_counter() - started, 3), bool(answer))
    except Exception as error:
        return RagAnswer("cloud_deepseek_rag", cloud_model(), "", round(time.perf_counter() - started, 3), False, str(error))


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


def run_question(
    question: str,
    query: str,
    index: Path,
    strategy: str,
    top_k: int,
    model: str,
    include_cloud: bool,
) -> dict[str, Any]:
    retrieval_started = time.perf_counter()
    chunks = retrieve_chunks(index, query or question, strategy, top_k)
    retrieval_elapsed = round(time.perf_counter() - retrieval_started, 3)
    local_answer = local_rag_answer(question, chunks, model)
    cloud_answer = cloud_rag_answer(question, chunks) if include_cloud else None
    return {
        "question": question,
        "query": query or question,
        "index_dir": str(index),
        "strategy": strategy,
        "top_k": top_k,
        "retrieval_local": True,
        "retrieval_elapsed_seconds": retrieval_elapsed,
        "chunks": chunk_payload(chunks),
        "local": asdict(local_answer),
        "cloud": asdict(cloud_answer) if cloud_answer else None,
        "evaluation": evaluate(local_answer, cloud_answer, chunks),
    }


def evaluate(local_answer: RagAnswer, cloud_answer: RagAnswer | None, chunks: list[RetrievedChunk]) -> dict[str, Any]:
    local_text = local_answer.answer.lower()
    source_ids = [f"[s{index}]" for index in range(1, len(chunks) + 1)]
    local_cites = sum(1 for source_id in source_ids if source_id in local_text)
    result = {
        "local_quality": "ok" if local_answer.ok and local_cites > 0 else "weak",
        "local_has_source_citations": local_cites > 0,
        "local_speed_seconds": local_answer.elapsed_seconds,
        "local_stable": local_answer.ok and not local_answer.error,
    }
    if cloud_answer:
        cloud_text = cloud_answer.answer.lower()
        cloud_cites = sum(1 for source_id in source_ids if source_id in cloud_text)
        result.update(
            {
                "cloud_quality": "ok" if cloud_answer.ok and cloud_cites > 0 else "weak",
                "cloud_has_source_citations": cloud_cites > 0,
                "cloud_speed_seconds": cloud_answer.elapsed_seconds,
                "cloud_stable": cloud_answer.ok and not cloud_answer.error,
                "faster_provider": "local" if local_answer.elapsed_seconds <= cloud_answer.elapsed_seconds else "cloud",
            }
        )
    return result


def question_by_id(qid_or_text: str) -> tuple[str, str]:
    for item in CONTROL_QUESTIONS:
        if item.qid == qid_or_text:
            return item.question, item.search_query
    return qid_or_text, qid_or_text


def build_report(payload: dict[str, Any]) -> str:
    rows = []
    details: list[str] = []
    for item in payload["results"]:
        cloud = item.get("cloud")
        evaluation = item["evaluation"]
        rows.append(
            "| {question} | {chunks} | {retrieval:.3f} | {local_quality} | {local_speed:.3f} | {cloud_quality} | {cloud_speed} | {faster} |".format(
                question=compact(item["question"], 80).replace("|", "\\|"),
                chunks=len(item["chunks"]),
                retrieval=float(item["retrieval_elapsed_seconds"]),
                local_quality=evaluation["local_quality"],
                local_speed=float(item["local"]["elapsed_seconds"]),
                cloud_quality=evaluation.get("cloud_quality", "skip"),
                cloud_speed=f"{float(cloud['elapsed_seconds']):.3f}" if cloud else "skip",
                faster=evaluation.get("faster_provider", "local-only"),
            )
        )
        details.extend(
            [
                f"### {compact(item['question'], 120)}",
                "",
                f"- retrieval_elapsed_seconds: `{item['retrieval_elapsed_seconds']}`",
                f"- local_model: `{item['local']['model']}`",
                f"- local_elapsed_seconds: `{item['local']['elapsed_seconds']}`",
                f"- cloud_model: `{cloud['model'] if cloud else 'skipped'}`",
                f"- cloud_elapsed_seconds: `{cloud['elapsed_seconds'] if cloud else 'skipped'}`",
                f"- local_quality: `{evaluation['local_quality']}`",
                f"- local_stable: `{evaluation['local_stable']}`",
                f"- local_has_source_citations: `{evaluation['local_has_source_citations']}`",
                f"- cloud_quality: `{evaluation.get('cloud_quality', 'skipped')}`",
                f"- cloud_stable: `{evaluation.get('cloud_stable', 'skipped')}`",
                f"- faster_provider: `{evaluation.get('faster_provider', 'local-only')}`",
                "",
                "**Retrieved Sources:**",
                "",
                *[f"- [S{idx}] `{chunk['source']}` | `{chunk['section']}` | score `{chunk['score']}`" for idx, chunk in enumerate(item["chunks"], start=1)],
                "",
                "**Local Answer:**",
                "",
                item["local"]["answer"] or item["local"].get("error", ""),
                "",
                "**Cloud Answer:**",
                "",
                (cloud["answer"] if cloud else "skipped") or (cloud.get("error", "") if cloud else "skipped"),
                "",
            ]
        )
    return "\n".join(
        [
            "# Day 28. Local LLM + RAG",
            "",
            "## Summary",
            "",
            f"- generated_at: `{payload['generated_at']}`",
            f"- fully_local_rag: `{payload['fully_local_rag']}`",
            f"- retrieval: `{payload['retrieval']}`",
            f"- generation: `{payload['generation']}`",
            f"- index_dir: `{payload['index_dir']}`",
            f"- local_model: `{payload['local_model']}`",
            f"- cloud_compare: `{payload['cloud_compare']}`",
            f"- questions: `{len(payload['results'])}`",
            f"- local_passed: `{payload['summary']['local_passed']}`",
            f"- cloud_passed: `{payload['summary']['cloud_passed']}`",
            f"- local_quality_ok: `{payload['summary']['local_quality_ok']}`",
            f"- cloud_quality_ok: `{payload['summary']['cloud_quality_ok']}`",
            f"- avg_retrieval_seconds: `{payload['summary']['avg_retrieval_seconds']}`",
            f"- avg_local_seconds: `{payload['summary']['avg_local_seconds']}`",
            f"- avg_cloud_seconds: `{payload['summary']['avg_cloud_seconds']}`",
            "",
            "## Comparison",
            "",
            "| question | chunks | retrieval s | local quality | local s | cloud quality | cloud s | faster |",
            "|---|---:|---:|---|---:|---|---:|---|",
            *rows,
            "",
            "## Details",
            "",
            *details,
            "## Commands",
            "",
            "```powershell",
            ".\\.venv\\Scripts\\python.exe day28_local_rag.py status",
            ".\\.venv\\Scripts\\python.exe day28_local_rag.py ask q01 --local-model qwen2.5:0.5b",
            ".\\.venv\\Scripts\\python.exe day28_local_rag.py compare q01 --local-model qwen2.5:0.5b",
            ".\\.venv\\Scripts\\python.exe day28_local_rag.py verify --limit 3 --local-model qwen2.5:0.5b",
            "```",
            "",
        ]
    )


def save_report(payload: dict[str, Any], report_path: str) -> tuple[Path, Path]:
    target = store_dir()
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "last_verify.json"
    json_path.write_text(to_json(payload), encoding="utf-8")
    report = Path(report_path).resolve()
    report.write_text(build_report(payload), encoding="utf-8")
    return json_path.resolve(), report


def common_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "index": index_dir(args),
        "strategy": args.strategy,
        "top_k": args.top_k,
        "model": local_model(args.local_model),
    }


def command_status(args: argparse.Namespace) -> int:
    start_ollama_if_needed()
    summary = load_summary(index_dir(args))
    print(f"ollama_url={ollama_url()}")
    print(f"ollama_models={', '.join(list_models())}")
    print(f"index_dir={index_dir(args)}")
    print(f"embedding_provider={summary['embedding_provider']}")
    print(f"embedding_model={summary['embedding_model']}")
    print(f"embedding_dim={summary['embedding_dim']}")
    print(f"strategy={args.strategy}")
    print(f"local_model={local_model(args.local_model)}")
    print(f"cloud_available={cloud_available()}")
    return 0


def command_ask(args: argparse.Namespace) -> int:
    start_ollama_if_needed()
    model = local_model(args.local_model)
    ensure_model(model)
    question, query = question_by_id(args.question)
    result = run_question(question, query, include_cloud=False, **common_kwargs(args))
    print(f"fully_local_rag=True")
    print(f"retrieval_local=True")
    print(f"generation=local_ollama")
    print(f"model={model}")
    print(f"question={question}")
    print(f"retrieved_chunks={len(result['chunks'])}")
    for index, chunk in enumerate(result["chunks"], start=1):
        print(f"[S{index}] {chunk['source']} | {chunk['section']} | score={chunk['score']}")
    print(f"elapsed_retrieval={result['retrieval_elapsed_seconds']}")
    print(f"elapsed_generation={result['local']['elapsed_seconds']}")
    print("answer:")
    print(result["local"]["answer"] or result["local"]["error"])
    return 0 if result["local"]["ok"] else 2


def command_compare(args: argparse.Namespace) -> int:
    start_ollama_if_needed()
    model = local_model(args.local_model)
    ensure_model(model)
    question, query = question_by_id(args.question)
    include_cloud = args.cloud == "yes" or (args.cloud == "auto" and cloud_available())
    result = run_question(question, query, include_cloud=include_cloud, **common_kwargs(args))
    print(f"question={question}")
    print(f"retrieved_chunks={len(result['chunks'])}")
    print(f"local_ok={result['local']['ok']} local_seconds={result['local']['elapsed_seconds']}")
    print("local_answer:")
    print(result["local"]["answer"] or result["local"]["error"])
    if result["cloud"]:
        print(f"cloud_ok={result['cloud']['ok']} cloud_seconds={result['cloud']['elapsed_seconds']}")
        print("cloud_answer:")
        print(result["cloud"]["answer"] or result["cloud"]["error"])
    else:
        print("cloud=skipped")
    print("evaluation:")
    print(to_json(result["evaluation"]))
    return 0 if result["local"]["ok"] else 2


def command_verify(args: argparse.Namespace) -> int:
    start_ollama_if_needed()
    model = local_model(args.local_model)
    ensure_model(model)
    include_cloud = args.cloud == "yes" or (args.cloud == "auto" and cloud_available())
    selected = CONTROL_QUESTIONS[: args.limit]
    results = [
        run_question(item.question, item.search_query, include_cloud=include_cloud, **common_kwargs(args))
        for item in selected
    ]
    cloud_results = [item for item in results if item.get("cloud")]
    avg_cloud_seconds = (
        round(sum(item["cloud"]["elapsed_seconds"] for item in cloud_results) / len(cloud_results), 3)
        if cloud_results
        else "skipped"
    )
    payload = {
        "generated_at": now_iso(),
        "fully_local_rag": True,
        "retrieval": "local SQLite index + local Ollama embeddings",
        "generation": "local Ollama chat model",
        "index_dir": str(index_dir(args)),
        "strategy": args.strategy,
        "top_k": args.top_k,
        "local_model": model,
        "cloud_compare": include_cloud,
        "results": results,
        "summary": {
            "local_passed": sum(1 for item in results if item["local"]["ok"]),
            "cloud_passed": sum(1 for item in results if item.get("cloud") and item["cloud"]["ok"]),
            "local_quality_ok": sum(1 for item in results if item["evaluation"]["local_quality"] == "ok"),
            "cloud_quality_ok": sum(1 for item in results if item["evaluation"].get("cloud_quality") == "ok"),
            "avg_retrieval_seconds": round(sum(item["retrieval_elapsed_seconds"] for item in results) / len(results), 3),
            "avg_local_seconds": round(sum(item["local"]["elapsed_seconds"] for item in results) / len(results), 3),
            "avg_cloud_seconds": avg_cloud_seconds,
        },
    }
    json_path, report_path = save_report(payload, args.report)
    print("fully_local_rag=True")
    print("retrieval=local SQLite index + local Ollama embeddings")
    print("generation=local Ollama chat model")
    print(f"cloud_compare={include_cloud}")
    for index, item in enumerate(results, start=1):
        print(
            f"q{index} local_ok={item['local']['ok']} local_seconds={item['local']['elapsed_seconds']} "
            f"retrieval_seconds={item['retrieval_elapsed_seconds']}"
        )
        if item["cloud"]:
            print(f"q{index} cloud_ok={item['cloud']['ok']} cloud_seconds={item['cloud']['elapsed_seconds']}")
    print(f"summary={to_json(payload['summary'])}")
    print(f"json_saved={json_path}")
    print(f"report_saved={report_path}")
    return 0 if payload["summary"]["local_passed"] == len(results) else 2


def add_common_args(parser: argparse.ArgumentParser) -> None:
    load_config()
    parser.add_argument("--index-dir", default=os.getenv("DAY28_RAG_INDEX_DIR", DEFAULT_INDEX_DIR))
    parser.add_argument("--strategy", default=os.getenv("DAY28_RAG_STRATEGY", DEFAULT_STRATEGY))
    parser.add_argument("--top-k", type=int, default=parse_int(os.getenv("DAY28_TOP_K"), DEFAULT_TOP_K))
    parser.add_argument("--local-model", default=None)


def main() -> None:
    load_config()
    parser = argparse.ArgumentParser(description="Day28 local LLM RAG. Retrieval and generation can run fully local.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status")
    add_common_args(status_parser)
    status_parser.set_defaults(func=command_status)

    ask_parser = subparsers.add_parser("ask")
    ask_parser.add_argument("question")
    add_common_args(ask_parser)
    ask_parser.set_defaults(func=command_ask)

    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("question")
    compare_parser.add_argument("--cloud", choices=["auto", "yes", "no"], default="auto")
    add_common_args(compare_parser)
    compare_parser.set_defaults(func=command_compare)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--limit", type=int, default=3)
    verify_parser.add_argument("--cloud", choices=["auto", "yes", "no"], default="auto")
    verify_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    add_common_args(verify_parser)
    verify_parser.set_defaults(func=command_verify)

    args = parser.parse_args()
    try:
        raise SystemExit(args.func(args))
    except Exception as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
