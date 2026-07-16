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


DEFAULT_INDEX_DIR = "day31_project_index_store"
DEFAULT_STORE_DIR = "day31_project_assistant_store"
DEFAULT_REPORT_PATH = "DAY31_PROJECT_ASSISTANT_REPORT.md"
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8011
DEFAULT_MCP_URL = f"http://{DEFAULT_MCP_HOST}:{DEFAULT_MCP_PORT}/mcp"
DEFAULT_EMBEDDER = "hash"
DEFAULT_TOP_K = 5
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_TIMEOUT_SECONDS = 60
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
THINKING_DISABLED = {"thinking": {"type": "disabled"}}


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class ProjectDoc:
    doc_id: str
    source: str
    title: str
    path: Path
    text: str
    sha256: str


@dataclass(frozen=True)
class ProjectChunk:
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
class HelpResult:
    question: str
    answer: str
    llm_used: bool
    mcp_connected: bool
    branch: str
    commit: str
    sources: list[dict[str, Any]]
    tokens: dict[str, int]
    elapsed_seconds: float
    error: str = ""


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
    return Path(value or os.getenv("DAY31_INDEX_DIR", DEFAULT_INDEX_DIR)).resolve()


def store_dir_from_env(value: str | None = None) -> Path:
    load_dotenv()
    return Path(value or os.getenv("DAY31_STORE_DIR", DEFAULT_STORE_DIR)).resolve()


def embedder_from_env(value: str | None = None) -> str:
    load_dotenv()
    return (value or os.getenv("DAY31_EMBEDDER", DEFAULT_EMBEDDER)).lower()


def db_path(index_dir: Path) -> Path:
    return index_dir / "project_docs.sqlite"


def summary_path(index_dir: Path) -> Path:
    return index_dir / "index_summary.json"


def collect_docs(root: Path) -> list[ProjectDoc]:
    candidates = [root / "README.md"]
    docs_dir = root / "docs"
    if docs_dir.exists():
        candidates.extend(sorted(docs_dir.rglob("*.md")))
    docs: list[ProjectDoc] = []
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        text = read_text(path)
        if not text or not text.strip():
            continue
        source = str(path.relative_to(root))
        doc_id = re.sub(r"[^a-zA-Z0-9]+", "_", source).strip("_").lower()
        docs.append(ProjectDoc(doc_id, source, path.name, path, text, sha256_text(text)))
    return docs


def token_count(text: str) -> int:
    return len(re.findall(r"[A-Za-zА-Яа-яЁё0-9_./:-]+", text))


def split_words(text: str, max_tokens: int = 220, overlap: int = 40) -> list[str]:
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


def markdown_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, list[str]]] = []
    current_title = "file"
    current_lines: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match and current_lines:
            sections.append((current_title, current_lines))
            current_title = match.group(2).strip()
            current_lines = [line]
        elif match:
            current_title = match.group(2).strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_title, current_lines))
    return [(title, "\n".join(lines).strip()) for title, lines in sections if "\n".join(lines).strip()]


def make_chunks(docs: list[ProjectDoc]) -> list[ProjectChunk]:
    chunks: list[ProjectChunk] = []
    for doc in docs:
        chunk_index = 0
        for section, text in markdown_sections(doc.text):
            for part in split_words(text):
                chunk_index += 1
                chunk_id = f"{doc.doc_id}_{chunk_index:03d}"
                chunks.append(
                    ProjectChunk(
                        chunk_id=chunk_id,
                        doc_id=doc.doc_id,
                        source=doc.source,
                        title=doc.title,
                        section=section,
                        chunk_index=chunk_index,
                        text=part,
                        token_count=token_count(part),
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
            embedding_model TEXT NOT NULL,
            FOREIGN KEY(doc_id) REFERENCES documents(doc_id)
        );

        CREATE TABLE meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def build_index(root: Path, index_dir: Path, provider: str) -> dict[str, Any]:
    docs = collect_docs(root)
    if not docs:
        raise FileNotFoundError("No README.md or docs/*.md files found for Day31 RAG index")
    chunks = make_chunks(docs)
    if not chunks:
        raise RuntimeError("No chunks created from project documentation")
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
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        raise FileNotFoundError(f"Day31 index not found: {path}. Run: day31_dev_assistant.py index")
    return json.loads(path.read_text(encoding="utf-8"))


def search_docs(index_dir: Path, question: str, top_k: int) -> list[RetrievedChunk]:
    summary = load_summary(index_dir)
    provider = summary["embedding_provider"]
    dim = int(summary["embedding_dim"])
    embedder = create_embedder("hash" if provider == "local_hashing_v1" else provider, dim)
    query_vector = embedder.embed(question)
    conn = sqlite3.connect(db_path(index_dir))
    try:
        rows = conn.execute(
            """
            SELECT chunk_id, source, title, section, text, embedding
            FROM chunks
            """
        ).fetchall()
    finally:
        conn.close()
    scored = []
    for row in rows:
        score = cosine_from_normalized(query_vector, blob_to_vector(row[5]))
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        RetrievedChunk(index, float(score), row[0], row[1], row[2], row[3], row[4])
        for index, (score, row) in enumerate(scored[:top_k], start=1)
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
                tools = []
                for tool in response.tools:
                    raw = model_to_dict(tool)
                    tools.append(
                        {
                            "name": raw.get("name"),
                            "description": raw.get("description"),
                            "input_schema": raw.get("inputSchema") or raw.get("input_schema"),
                        }
                    )
                return tools

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


def mcp_url_from_args(args: argparse.Namespace) -> str:
    return args.mcp_url or os.getenv("DAY31_MCP_URL", DEFAULT_MCP_URL)


def start_mcp_server(root: Path, store_dir: Path, host: str, port: int) -> subprocess.Popen[str]:
    store_dir.mkdir(parents=True, exist_ok=True)
    stdout = (store_dir / "mcp_server.stdout.log").open("w", encoding="utf-8")
    stderr = (store_dir / "mcp_server.stderr.log").open("w", encoding="utf-8")
    return subprocess.Popen(
        [
            sys.executable,
            "day31_project_mcp_server.py",
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
        stdout=stdout,
        stderr=stderr,
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


def ensure_index(root: Path, index_dir: Path, provider: str) -> dict[str, Any]:
    if summary_path(index_dir).exists() and db_path(index_dir).exists():
        return load_summary(index_dir)
    return build_index(root, index_dir, provider)


def deepseek_client() -> tuple[OpenAI, str]:
    load_dotenv()
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    model = os.getenv("DAY31_DEEPSEEK_MODEL") or os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    timeout = parse_int(os.getenv("DAY31_DEEPSEEK_TIMEOUT_SECONDS"), DEFAULT_TIMEOUT_SECONDS)
    return OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL, timeout=timeout, max_retries=0), model


def build_prompt(question: str, chunks: list[RetrievedChunk], branch: dict[str, Any]) -> list[dict[str, str]]:
    context = "\n\n".join(
        [
            (
                f"[source={chunk.source} section={chunk.section} chunk_id={chunk.chunk_id} score={chunk.score:.4f}]\n"
                f"{chunk.text}"
            )
            for chunk in chunks
        ]
    )
    mcp_context = to_json(branch)
    return [
        {
            "role": "system",
            "content": (
                "Ты ассистент разработчика для текущего проекта. "
                "Отвечай по-русски. Используй только RAG-документацию и MCP-контекст. "
                "Если в документации нет ответа, так и скажи. "
                "Всегда указывай текущую git-ветку и источники."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Вопрос: {question}\n\n"
                f"MCP context:\n{mcp_context}\n\n"
                f"RAG context:\n{context}\n\n"
                "Формат ответа:\n"
                "Ответ: ...\n"
                "Git: branch=..., commit=...\n"
                "Источники: source + section + chunk_id"
            ),
        },
    ]


def fallback_answer(question: str, chunks: list[RetrievedChunk], branch: dict[str, Any], error: str) -> HelpResult:
    sources = [asdict(chunk) for chunk in chunks]
    bullets = "\n".join(
        [f"- {chunk.source} / {chunk.section}: {re.sub(r'\\s+', ' ', chunk.text).strip()[:220]}" for chunk in chunks]
    )
    answer = (
        "Ответ: LLM недоступна, поэтому показываю найденный RAG-контекст проекта.\n"
        f"Вопрос: {question}\n\n"
        f"{bullets}\n\n"
        f"Git: branch={branch.get('branch', '')}, commit={branch.get('commit', '')}\n"
        "Источники: см. список ниже."
    )
    return HelpResult(
        question=question,
        answer=answer,
        llm_used=False,
        mcp_connected=bool(branch.get("branch")),
        branch=str(branch.get("branch", "")),
        commit=str(branch.get("commit", "")),
        sources=sources,
        tokens={"prompt": 0, "completion": 0, "total": 0},
        elapsed_seconds=0.0,
        error=error,
    )


def ask_llm(question: str, chunks: list[RetrievedChunk], branch: dict[str, Any]) -> HelpResult:
    messages = build_prompt(question, chunks, branch)
    started = time.perf_counter()
    try:
        client, model = deepseek_client()
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            max_tokens=800,
            extra_body=THINKING_DISABLED,
        )
        answer = response.choices[0].message.content or ""
        usage = response.usage
        return HelpResult(
            question=question,
            answer=answer,
            llm_used=True,
            mcp_connected=bool(branch.get("branch")),
            branch=str(branch.get("branch", "")),
            commit=str(branch.get("commit", "")),
            sources=[asdict(chunk) for chunk in chunks],
            tokens={
                "prompt": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion": int(getattr(usage, "completion_tokens", 0) or 0),
                "total": int(getattr(usage, "total_tokens", 0) or 0),
            },
            elapsed_seconds=round(time.perf_counter() - started, 3),
        )
    except (APIConnectionError, APIError, APIStatusError, APITimeoutError, RuntimeError) as exc:
        result = fallback_answer(question, chunks, branch, str(exc))
        return HelpResult(
            **{**asdict(result), "elapsed_seconds": round(time.perf_counter() - started, 3)}
        )


def run_help(args: argparse.Namespace) -> HelpResult:
    load_dotenv()
    root = Path(args.project_root).resolve()
    index_dir = index_dir_from_env(args.index_dir)
    store_dir = store_dir_from_env(args.store_dir)
    provider = embedder_from_env(args.embedder)
    ensure_index(root, index_dir, provider)

    mcp_url = mcp_url_from_args(args)
    spawned: subprocess.Popen[str] | None = None
    if not args.mcp_url and not args.no_spawn_mcp:
        spawned = start_mcp_server(root, store_dir, DEFAULT_MCP_HOST, DEFAULT_MCP_PORT)
        wait_for_mcp(mcp_url, 12)
    try:
        branch = asyncio.run(call_mcp_tool(mcp_url, "get_git_branch", {}, 15))
        chunks = search_docs(index_dir, args.question, args.top_k)
        result = ask_llm(args.question, chunks, branch)
        store_dir.mkdir(parents=True, exist_ok=True)
        (store_dir / "last_help.json").write_text(to_json(asdict(result)), encoding="utf-8")
        return result
    finally:
        stop_mcp_server(spawned)


def print_result(result: HelpResult) -> None:
    print("command=/help")
    print(f"mcp_connected={result.mcp_connected}")
    print(f"git_branch={result.branch}")
    print(f"git_commit={result.commit}")
    print(f"llm_used={result.llm_used}")
    if result.error:
        print(f"llm_error={result.error}")
    print(f"tokens={to_json(result.tokens)}")
    print("answer:")
    print(result.answer)
    print("sources:")
    for source in result.sources:
        print(
            f"- score={source['score']:.4f} source={source['source']} "
            f"section={source['section']} chunk_id={source['chunk_id']}"
        )


def build_report(result: HelpResult, index_summary: dict[str, Any], tools: list[dict[str, Any]]) -> str:
    return "\n".join(
        [
            "# Day31. Developer Assistant With RAG And MCP",
            "",
            "## Summary",
            "",
            f"- generated_at: `{now_iso()}`",
            f"- project_root: `{index_summary['project_root']}`",
            f"- indexed_sources: `{', '.join(index_summary['sources'])}`",
            f"- chunks: `{index_summary['chunks']}`",
            f"- embedding_provider: `{index_summary['embedding_provider']}`",
            f"- mcp_tools: `{', '.join(tool['name'] for tool in tools)}`",
            f"- git_branch_from_mcp: `{result.branch}`",
            f"- llm_used: `{result.llm_used}`",
            "",
            "## /help Result",
            "",
            f"- question: `{result.question}`",
            "",
            result.answer,
            "",
            "## Sources",
            "",
            "| score | source | section | chunk_id |",
            "|---:|---|---|---|",
            *[
                f"| {source['score']:.4f} | {source['source']} | {source['section']} | {source['chunk_id']} |"
                for source in result.sources
            ],
            "",
            "## MCP Tools",
            "",
            "```json",
            to_json(tools),
            "```",
            "",
            "## Check Commands",
            "",
            "```powershell",
            ".\\.venv\\Scripts\\python.exe day31_dev_assistant.py index",
            ".\\.venv\\Scripts\\python.exe day31_dev_assistant.py mcp-tools",
            ".\\.venv\\Scripts\\python.exe day31_dev_assistant.py /help \"Какая структура проекта?\"",
            ".\\.venv\\Scripts\\python.exe day31_dev_assistant.py demo",
            "```",
            "",
        ]
    )


def command_index(args: argparse.Namespace) -> int:
    summary = build_index(Path(args.project_root).resolve(), index_dir_from_env(args.index_dir), embedder_from_env(args.embedder))
    print(f"indexed_sources={len(summary['sources'])}")
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
            print(f"- {tool['name']}: {tool['description']}")
        return 0
    finally:
        stop_mcp_server(spawned)


def command_mcp_branch(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    store_dir = store_dir_from_env(args.store_dir)
    url = mcp_url_from_args(args)
    spawned = None
    if not args.mcp_url and not args.no_spawn_mcp:
        spawned = start_mcp_server(root, store_dir, DEFAULT_MCP_HOST, DEFAULT_MCP_PORT)
        wait_for_mcp(url, 12)
    try:
        branch = asyncio.run(call_mcp_tool(url, "get_git_branch", {}, 15))
        print("connected=True")
        print(f"branch={branch.get('branch')}")
        print(f"commit={branch.get('commit')}")
        return 0
    finally:
        stop_mcp_server(spawned)


def command_help(args: argparse.Namespace) -> int:
    result = run_help(args)
    print_result(result)
    return 0 if result.mcp_connected and result.sources else 2


def command_demo(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    index_dir = index_dir_from_env(args.index_dir)
    provider = embedder_from_env(args.embedder)
    index_summary = build_index(root, index_dir, provider)
    tools_args = argparse.Namespace(**vars(args))
    tools_args.no_spawn_mcp = False
    tools_args.mcp_url = None
    store_dir = store_dir_from_env(args.store_dir)
    spawned = start_mcp_server(root, store_dir, DEFAULT_MCP_HOST, DEFAULT_MCP_PORT)
    try:
        tools = wait_for_mcp(DEFAULT_MCP_URL, 12)
    finally:
        stop_mcp_server(spawned)
    help_args = argparse.Namespace(**vars(args))
    help_args.question = args.question
    help_args.top_k = args.top_k
    result = run_help(help_args)
    report_path = Path(args.report).resolve()
    report_path.write_text(build_report(result, index_summary, tools), encoding="utf-8")
    print_result(result)
    print(f"report_saved={report_path}")
    return 0 if result.mcp_connected and result.sources else 2


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--index-dir", default=None)
    parser.add_argument("--store-dir", default=None)


def add_mcp(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mcp-url", default=None)
    parser.add_argument("--no-spawn-mcp", action="store_true")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Day31 developer assistant with project RAG and MCP context.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index")
    add_common(index_parser)
    index_parser.add_argument("--embedder", choices=["hash", "ollama"], default=None)
    index_parser.set_defaults(func=command_index)

    tools_parser = subparsers.add_parser("mcp-tools")
    add_common(tools_parser)
    add_mcp(tools_parser)
    tools_parser.set_defaults(func=command_mcp_tools)

    branch_parser = subparsers.add_parser("mcp-branch")
    add_common(branch_parser)
    add_mcp(branch_parser)
    branch_parser.set_defaults(func=command_mcp_branch)

    help_parser = subparsers.add_parser("/help")
    help_parser.add_argument("question")
    add_common(help_parser)
    add_mcp(help_parser)
    help_parser.add_argument("--embedder", choices=["hash", "ollama"], default=None)
    help_parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    help_parser.set_defaults(func=command_help)

    demo_parser = subparsers.add_parser("demo")
    add_common(demo_parser)
    add_mcp(demo_parser)
    demo_parser.add_argument("--embedder", choices=["hash", "ollama"], default=None)
    demo_parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    demo_parser.add_argument("--question", default="Какая структура проекта и какая текущая git-ветка?")
    demo_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    demo_parser.set_defaults(func=command_demo)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
