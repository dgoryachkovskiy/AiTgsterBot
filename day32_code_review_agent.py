import argparse
import asyncio
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI

from day21_document_indexer import (
    blob_to_vector,
    cosine_from_normalized,
    create_embedder,
    hash_embedding_dim_from_env,
    read_text,
    sha256_text,
    vector_to_blob,
)


DEFAULT_INDEX_DIR = "day32_code_review_index_store"
DEFAULT_STORE_DIR = "day32_code_review_store"
DEFAULT_REPORT_PATH = "DAY32_AI_REVIEW.md"
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8012
DEFAULT_MCP_URL = f"http://{DEFAULT_MCP_HOST}:{DEFAULT_MCP_PORT}/mcp"
DEFAULT_EMBEDDER = "hash"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_MAX_DIFF_CHARS = 60000
DEFAULT_TOP_K = 8
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
THINKING_DISABLED = {"thinking": {"type": "disabled"}}
INDEX_EXTENSIONS = {".py", ".json", ".yaml", ".yml", ".toml"}
EXCLUDED_DIRS = {
    ".git",
    ".idea",
    ".venv",
    "__pycache__",
    "day21_index_store",
    "day22_rag_store",
    "day23_rag_store",
    "day24_rag_store",
    "day25_chat_store",
    "day26_local_llm_store",
    "day27_local_llm_app_store",
    "day28_local_rag_store",
    "day29_optimization_store",
    "day30_private_llm_store",
    "day31_project_index_store",
    "day31_project_assistant_store",
    "day32_code_review_index_store",
    "day32_code_review_store",
    "memory_layers_store",
}


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class IndexedDoc:
    doc_id: str
    source: str
    title: str
    path: Path
    text: str
    sha256: str


@dataclass(frozen=True)
class IndexedChunk:
    chunk_id: str
    doc_id: str
    source: str
    title: str
    section: str
    chunk_index: int
    text: str
    token_count: int


@dataclass(frozen=True)
class RetrievedChunk:
    rank: int
    score: float
    chunk_id: str
    source: str
    title: str
    section: str
    text: str


@dataclass(frozen=True)
class ReviewResult:
    generated_at: str
    base_ref: str
    head_ref: str
    branch: str
    commit: str
    changed_files: list[dict[str, Any]]
    diff_truncated: bool
    diff_chars: int
    rag_sources: list[dict[str, Any]]
    review_markdown: str
    tokens: dict[str, int]
    elapsed_seconds: float
    llm_used: bool


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def parse_int(value: str | None, default: int) -> int:
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def index_dir_from_env(value: str | None = None) -> Path:
    load_dotenv()
    return Path(value or os.getenv("DAY32_INDEX_DIR", DEFAULT_INDEX_DIR)).resolve()


def store_dir_from_env(value: str | None = None) -> Path:
    load_dotenv()
    return Path(value or os.getenv("DAY32_STORE_DIR", DEFAULT_STORE_DIR)).resolve()


def embedder_from_env(value: str | None = None) -> str:
    load_dotenv()
    return (value or os.getenv("DAY32_EMBEDDER", DEFAULT_EMBEDDER)).lower()


def db_path(index_dir: Path) -> Path:
    return index_dir / "code_review_index.sqlite"


def summary_path(index_dir: Path) -> Path:
    return index_dir / "index_summary.json"


def token_count(text: str) -> int:
    return len(re.findall(r"[A-Za-zА-Яа-яЁё0-9_./:-]+", text))


def should_skip(path: Path) -> bool:
    return any(part in EXCLUDED_DIRS for part in path.parts)


def should_index(path: Path, root: Path) -> bool:
    if should_skip(path) or not path.is_file():
        return False
    relative = path.relative_to(root)
    rel = str(relative).replace("\\", "/")
    if rel == "README.md":
        return True
    if rel.startswith("docs/") and path.suffix.lower() == ".md":
        return True
    if rel.startswith(".github/workflows/") and path.suffix.lower() in {".yml", ".yaml"}:
        return True
    if path.name in {".env.example", "requirements.txt"}:
        return True
    return path.suffix.lower() in INDEX_EXTENSIONS


def collect_documents(root: Path) -> list[IndexedDoc]:
    docs: list[IndexedDoc] = []
    for path in sorted(root.rglob("*")):
        if not should_index(path, root):
            continue
        text = read_text(path)
        if not text or not text.strip():
            continue
        source = str(path.relative_to(root))
        doc_id = re.sub(r"[^a-zA-Z0-9]+", "_", source).strip("_").lower()
        docs.append(IndexedDoc(doc_id, source, path.name, path, text, sha256_text(text)))
    return docs


def split_chunks(text: str, max_tokens: int = 260, overlap: int = 45) -> list[str]:
    words = re.findall(r"\S+", text)
    if len(words) <= max_tokens:
        return [text.strip()]
    chunks = []
    start = 0
    while start < len(words):
        end = min(len(words), start + max_tokens)
        chunks.append(" ".join(words[start:end]).strip())
        if end == len(words):
            break
        start = max(0, end - overlap)
    return chunks


def section_title(text: str, fallback: str) -> str:
    for line in text.splitlines()[:40]:
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            return heading.group(2).strip()
        func = re.match(r"^\s*(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
        if func:
            return f"{func.group(1)} {func.group(2)}"
    return fallback


def make_chunks(docs: list[IndexedDoc]) -> list[IndexedChunk]:
    chunks: list[IndexedChunk] = []
    for doc in docs:
        for index, text in enumerate(split_chunks(doc.text), start=1):
            chunks.append(
                IndexedChunk(
                    chunk_id=f"{doc.doc_id}_{index:03d}",
                    doc_id=doc.doc_id,
                    source=doc.source,
                    title=doc.title,
                    section=section_title(text, doc.title),
                    chunk_index=index,
                    text=text,
                    token_count=token_count(text),
                )
            )
    return chunks


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS chunks;
        DROP TABLE IF EXISTS documents;
        DROP TABLE IF EXISTS meta;

        CREATE TABLE documents (
            doc_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_chars INTEGER NOT NULL
        );

        CREATE TABLE chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            section TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            token_count INTEGER NOT NULL,
            embedding BLOB NOT NULL,
            embedding_dim INTEGER NOT NULL,
            embedding_provider TEXT NOT NULL,
            embedding_model TEXT NOT NULL
        );

        CREATE TABLE meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def build_index(root: Path, index_dir: Path, provider: str) -> dict[str, Any]:
    docs = collect_documents(root)
    if not docs:
        raise FileNotFoundError("No project docs/code files found for Day32 RAG index")
    chunks = make_chunks(docs)
    embedder = create_embedder(provider, hash_embedding_dim_from_env() if provider == "hash" else None)
    index_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path(index_dir))
    try:
        init_db(conn)
        conn.executemany(
            "INSERT INTO documents(doc_id, source, title, path, sha256, size_chars) VALUES (?, ?, ?, ?, ?, ?)",
            [(doc.doc_id, doc.source, doc.title, str(doc.path), doc.sha256, len(doc.text)) for doc in docs],
        )
        rows = []
        for chunk in chunks:
            vector = embedder.embed(chunk.text)
            rows.append(
                (
                    chunk.chunk_id,
                    chunk.doc_id,
                    chunk.source,
                    chunk.title,
                    chunk.section,
                    chunk.chunk_index,
                    chunk.text,
                    chunk.token_count,
                    vector_to_blob(vector),
                    len(vector),
                    embedder.provider,
                    embedder.model,
                )
            )
        conn.executemany(
            """
            INSERT INTO chunks(
                chunk_id, doc_id, source, title, section, chunk_index, text, token_count,
                embedding, embedding_dim, embedding_provider, embedding_model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        summary = {
            "generated_at": now_iso(),
            "project_root": str(root),
            "db_path": str(db_path(index_dir)),
            "documents": len(docs),
            "chunks": len(chunks),
            "sources": [doc.source for doc in docs],
            "embedding_provider": embedder.provider,
            "embedding_model": embedder.model,
            "embedding_dim": getattr(embedder, "dim", 0),
        }
        conn.execute("INSERT INTO meta(key, value) VALUES (?, ?)", ("summary", to_json(summary)))
        conn.commit()
    finally:
        conn.close()
    summary_path(index_dir).write_text(to_json(summary), encoding="utf-8")
    return summary


def load_summary(index_dir: Path) -> dict[str, Any]:
    path = summary_path(index_dir)
    if not path.exists():
        raise FileNotFoundError(f"Day32 index missing: {path}. Run day32_code_review_agent.py index")
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_index(root: Path, index_dir: Path, provider: str) -> dict[str, Any]:
    if db_path(index_dir).exists() and summary_path(index_dir).exists():
        return load_summary(index_dir)
    return build_index(root, index_dir, provider)


def search_index(index_dir: Path, query: str, top_k: int) -> list[RetrievedChunk]:
    summary = load_summary(index_dir)
    provider = summary["embedding_provider"]
    dim = int(summary["embedding_dim"])
    embedder = create_embedder("hash" if provider == "local_hashing_v1" else provider, dim)
    query_vector = embedder.embed(query)
    conn = sqlite3.connect(db_path(index_dir))
    try:
        rows = conn.execute(
            "SELECT chunk_id, source, title, section, text, embedding FROM chunks",
        ).fetchall()
    finally:
        conn.close()
    scored = []
    for row in rows:
        score = cosine_from_normalized(query_vector, blob_to_vector(row[5]))
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        RetrievedChunk(rank, float(score), row[0], row[1], row[2], row[3], row[4])
        for rank, (score, row) in enumerate(scored[:top_k], start=1)
    ]


def model_to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True)
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return value
    return {"value": value}


def extract_mcp_json(call_result: Any) -> dict[str, Any]:
    raw = model_to_dict(call_result)
    if raw.get("structuredContent"):
        return raw["structuredContent"]
    if raw.get("structured_content"):
        return raw["structured_content"]
    content = raw.get("content") or getattr(call_result, "content", []) or []
    for item in content:
        item_raw = model_to_dict(item)
        text = item_raw.get("text") or getattr(item, "text", "")
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return raw


async def list_mcp_tools(url: str, timeout_seconds: float) -> list[dict[str, Any]]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def run_session() -> list[dict[str, Any]]:
        async with streamable_http_client(url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                response = await session.list_tools()
                return [model_to_dict(tool) for tool in response.tools]

    return await asyncio.wait_for(run_session(), timeout=timeout_seconds)


async def call_mcp_tool(url: str, tool_name: str, args: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def run_session() -> dict[str, Any]:
        async with streamable_http_client(url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, args)
                return extract_mcp_json(result)

    return await asyncio.wait_for(run_session(), timeout=timeout_seconds)


def start_mcp_server(root: Path, store_dir: Path, host: str, port: int) -> subprocess.Popen[str]:
    store_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [
            sys.executable,
            "day32_review_mcp_server.py",
            "serve",
            "--host",
            host,
            "--port",
            str(port),
            "--project-root",
            str(root),
        ],
        cwd=root,
        text=True,
        stdout=(store_dir / "mcp_server.stdout.log").open("w", encoding="utf-8"),
        stderr=(store_dir / "mcp_server.stderr.log").open("w", encoding="utf-8"),
    )


def stop_mcp_server(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def wait_for_mcp(url: str, timeout_seconds: float) -> list[dict[str, Any]]:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            return asyncio.run(list_mcp_tools(url, 5))
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"MCP server did not become ready: {last_error}")


def mcp_url_from_args(args: argparse.Namespace) -> str:
    return args.mcp_url or os.getenv("DAY32_MCP_URL", DEFAULT_MCP_URL)


def deepseek_client() -> tuple[OpenAI, str]:
    load_dotenv()
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set. In GitHub Actions map secrets.DEPSEEK to DEEPSEEK_API_KEY.")
    model = os.getenv("DAY32_DEEPSEEK_MODEL") or os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    timeout = parse_int(os.getenv("DAY32_DEEPSEEK_TIMEOUT_SECONDS"), DEFAULT_TIMEOUT_SECONDS)
    return OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL, timeout=timeout, max_retries=0), model


def compact_file_list(changed_files: list[dict[str, Any]]) -> str:
    return "\n".join([f"- {item.get('status')}: {item.get('path')}" for item in changed_files])


def build_rag_query(changed_files: list[dict[str, Any]], diff: str) -> str:
    paths = " ".join(str(item.get("path", "")) for item in changed_files)
    diff_words = re.sub(r"\s+", " ", diff[:12000])
    return f"code review architecture bugs recommendations {paths} {diff_words}"


def unique_chunks(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    seen = set()
    result = []
    for chunk in chunks:
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        result.append(chunk)
    return result


def collect_rag_context(index_dir: Path, changed_files: list[dict[str, Any]], diff: str, top_k: int) -> list[RetrievedChunk]:
    chunks = search_index(index_dir, build_rag_query(changed_files, diff), top_k)
    for item in changed_files[:8]:
        chunks.extend(search_index(index_dir, str(item.get("path", "")), 2))
    return unique_chunks(chunks)[: max(top_k, 8)]


def collect_snapshots(mcp_url: str, changed_files: list[dict[str, Any]], head_ref: str) -> list[dict[str, Any]]:
    snapshots = []
    for item in changed_files:
        if len(snapshots) >= 5:
            break
        status = str(item.get("status", ""))
        path = str(item.get("path", ""))
        if not path or status.startswith("D"):
            continue
        try:
            snapshots.append(asyncio.run(call_mcp_tool(mcp_url, "get_file_snapshot", {"path": path, "ref": head_ref, "max_chars": 12000}, 15)))
        except Exception as exc:
            snapshots.append({"path": path, "error": str(exc)})
    return snapshots


def build_review_prompt(
    base_ref: str,
    head_ref: str,
    branch: dict[str, Any],
    changed: dict[str, Any],
    diff_data: dict[str, Any],
    rag_chunks: list[RetrievedChunk],
    snapshots: list[dict[str, Any]],
) -> list[dict[str, str]]:
    rag_context = "\n\n".join(
        [
            (
                f"[source={chunk.source} section={chunk.section} chunk_id={chunk.chunk_id} score={chunk.score:.4f}]\n"
                f"{chunk.text[:1800]}"
            )
            for chunk in rag_chunks
        ]
    )
    snapshot_context = "\n\n".join(
        [
            f"[file={item.get('path')} truncated={item.get('truncated')}]\n{str(item.get('content') or item.get('error') or '')[:2500]}"
            for item in snapshots
        ]
    )
    return [
        {
            "role": "system",
            "content": (
                "Ты строгий AI reviewer кода. Отвечай по-русски. "
                "Ищи реальные баги, регрессии, архитектурные проблемы и конкретные рекомендации. "
                "Не выдумывай проблемы без опоры на diff/RAG. Если серьезных проблем нет, скажи это явно. "
                "Формат строго markdown с разделами: Потенциальные баги, Архитектурные проблемы, "
                "Рекомендации, RAG источники, Diff context."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Base ref: {base_ref}\nHead ref: {head_ref}\n"
                f"MCP branch context:\n{to_json(branch)}\n\n"
                f"Changed files:\n{compact_file_list(changed.get('files', []))}\n\n"
                f"Diff metadata: chars={diff_data.get('diff_chars')} truncated={diff_data.get('truncated')}\n"
                f"Unified diff:\n```diff\n{diff_data.get('diff', '')}\n```\n\n"
                f"Relevant RAG context:\n{rag_context}\n\n"
                f"Changed file snapshots:\n{snapshot_context}\n\n"
                "Требования к ответу:\n"
                "- упоминай конкретные пути файлов;\n"
                "- для каждого найденного риска объясняй почему это риск;\n"
                "- добавь источники RAG как source + section + chunk_id;\n"
                "- не добавляй общие советы без связи с diff."
            ),
        },
    ]


def call_deepseek(messages: list[dict[str, str]]) -> tuple[str, dict[str, int], float]:
    started = time.perf_counter()
    client, model = deepseek_client()
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
        max_tokens=1800,
        extra_body=THINKING_DISABLED,
    )
    usage = response.usage
    tokens = {
        "prompt": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion": int(getattr(usage, "completion_tokens", 0) or 0),
        "total": int(getattr(usage, "total_tokens", 0) or 0),
    }
    return response.choices[0].message.content or "", tokens, round(time.perf_counter() - started, 3)


def render_review(result: ReviewResult) -> str:
    meta = [
        "# Day32 AI Code Review",
        "",
        f"- generated_at: `{result.generated_at}`",
        f"- base_ref: `{result.base_ref}`",
        f"- head_ref: `{result.head_ref}`",
        f"- branch: `{result.branch}`",
        f"- commit: `{result.commit}`",
        f"- changed_files: `{len(result.changed_files)}`",
        f"- diff_chars: `{result.diff_chars}`",
        f"- diff_truncated: `{result.diff_truncated}`",
        f"- llm_used: `{result.llm_used}`",
        f"- tokens: `{to_json(result.tokens)}`",
        "",
        result.review_markdown.strip(),
        "",
        "## Machine Context",
        "",
        "### Changed Files",
        "",
        *[f"- `{item.get('status')}` `{item.get('path')}`" for item in result.changed_files],
        "",
        "### RAG Sources",
        "",
        "| score | source | section | chunk_id |",
        "|---:|---|---|---|",
        *[
            f"| {item['score']:.4f} | {item['source']} | {item['section']} | {item['chunk_id']} |"
            for item in result.rag_sources
        ],
        "",
    ]
    return "\n".join(meta)


def run_review(args: argparse.Namespace) -> ReviewResult:
    load_dotenv()
    root = Path(args.project_root).resolve()
    index_dir = index_dir_from_env(args.index_dir)
    store_dir = store_dir_from_env(args.store_dir)
    provider = embedder_from_env(args.embedder)
    ensure_index(root, index_dir, provider)

    mcp_url = mcp_url_from_args(args)
    spawned = None
    if not args.mcp_url and not args.no_spawn_mcp:
        spawned = start_mcp_server(root, store_dir, DEFAULT_MCP_HOST, DEFAULT_MCP_PORT)
        wait_for_mcp(mcp_url, 12)
    try:
        branch = asyncio.run(call_mcp_tool(mcp_url, "get_git_branch", {}, 15))
        changed = asyncio.run(call_mcp_tool(mcp_url, "get_changed_files", {"base_ref": args.base_ref, "head_ref": args.head_ref}, 30))
        diff_data = asyncio.run(
            call_mcp_tool(
                mcp_url,
                "get_pr_diff",
                {"base_ref": args.base_ref, "head_ref": args.head_ref, "max_chars": args.max_diff_chars},
                30,
            )
        )
        changed_files = changed.get("files", [])
        diff = diff_data.get("diff", "")
        rag_chunks = collect_rag_context(index_dir, changed_files, diff, args.top_k)
        snapshots = collect_snapshots(mcp_url, changed_files, args.head_ref)
        messages = build_review_prompt(args.base_ref, args.head_ref, branch, changed, diff_data, rag_chunks, snapshots)
        review_markdown, tokens, elapsed = call_deepseek(messages)
        result = ReviewResult(
            generated_at=now_iso(),
            base_ref=args.base_ref,
            head_ref=args.head_ref,
            branch=str(branch.get("branch", "")),
            commit=str(branch.get("commit", "")),
            changed_files=changed_files,
            diff_truncated=bool(diff_data.get("truncated")),
            diff_chars=int(diff_data.get("diff_chars", len(diff))),
            rag_sources=[asdict(chunk) for chunk in rag_chunks],
            review_markdown=review_markdown,
            tokens=tokens,
            elapsed_seconds=elapsed,
            llm_used=True,
        )
        store_dir.mkdir(parents=True, exist_ok=True)
        (store_dir / "last_review.json").write_text(to_json(asdict(result)), encoding="utf-8")
        return result
    finally:
        stop_mcp_server(spawned)


def command_index(args: argparse.Namespace) -> int:
    summary = build_index(Path(args.project_root).resolve(), index_dir_from_env(args.index_dir), embedder_from_env(args.embedder))
    print(f"indexed_documents={summary['documents']}")
    print(f"chunks={summary['chunks']}")
    print(f"embedding_provider={summary['embedding_provider']}")
    print(f"db_path={summary['db_path']}")
    return 0


def command_mcp_tools(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    store_dir = store_dir_from_env(args.store_dir)
    url = mcp_url_from_args(args)
    spawned = None
    if not args.mcp_url and not args.no_spawn_mcp:
        spawned = start_mcp_server(root, store_dir, DEFAULT_MCP_HOST, DEFAULT_MCP_PORT)
    try:
        tools = wait_for_mcp(url, 12)
        print(f"mcp_url={url}")
        print("connected=True")
        print(f"tools_count={len(tools)}")
        for tool in tools:
            print(f"- {tool.get('name')}: {tool.get('description')}")
        return 0
    finally:
        stop_mcp_server(spawned)


def command_review(args: argparse.Namespace) -> int:
    result = run_review(args)
    output = Path(args.output).resolve()
    output.write_text(render_review(result), encoding="utf-8")
    print(f"review_saved={output}")
    print(f"changed_files={len(result.changed_files)}")
    print(f"diff_truncated={result.diff_truncated}")
    print(f"llm_used={result.llm_used}")
    print(f"tokens={to_json(result.tokens)}")
    return 0


def command_demo(args: argparse.Namespace) -> int:
    args.output = args.output or DEFAULT_REPORT_PATH
    return command_review(args)


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--index-dir", default=None)
    parser.add_argument("--store-dir", default=None)
    parser.add_argument("--embedder", choices=["hash", "ollama"], default=None)


def add_mcp(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mcp-url", default=None)
    parser.add_argument("--no-spawn-mcp", action="store_true")


def add_review_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-ref", default="HEAD~1")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--max-diff-chars", type=int, default=parse_int(os.getenv("DAY32_MAX_DIFF_CHARS"), DEFAULT_MAX_DIFF_CHARS))
    parser.add_argument("--top-k", type=int, default=parse_int(os.getenv("DAY32_TOP_K"), DEFAULT_TOP_K))
    parser.add_argument("--output", default=DEFAULT_REPORT_PATH)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Day32 AI code review agent with GitHub Actions, RAG, and MCP.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index")
    add_common(index_parser)
    index_parser.set_defaults(func=command_index)

    tools_parser = subparsers.add_parser("mcp-tools")
    add_common(tools_parser)
    add_mcp(tools_parser)
    tools_parser.set_defaults(func=command_mcp_tools)

    review_parser = subparsers.add_parser("review")
    add_common(review_parser)
    add_mcp(review_parser)
    add_review_args(review_parser)
    review_parser.set_defaults(func=command_review)

    demo_parser = subparsers.add_parser("demo")
    add_common(demo_parser)
    add_mcp(demo_parser)
    add_review_args(demo_parser)
    demo_parser.set_defaults(func=command_demo)

    args = parser.parse_args()
    try:
        raise SystemExit(args.func(args))
    except (APIConnectionError, APIError, APIStatusError, APITimeoutError, RuntimeError) as exc:
        print(f"day32_error={exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
