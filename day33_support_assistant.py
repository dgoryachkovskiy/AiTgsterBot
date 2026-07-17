from __future__ import annotations

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


DEFAULT_INDEX_DIR = "day33_support_index_store"
DEFAULT_STORE_DIR = "day33_support_store"
DEFAULT_KB_DIR = "day33_support_knowledge"
DEFAULT_CRM_JSON = "day33_support_crm.json"
DEFAULT_REPORT_PATH = "DAY33_SUPPORT_ASSISTANT_REPORT.md"
DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8034
DEFAULT_MCP_URL = f"http://{DEFAULT_MCP_HOST}:{DEFAULT_MCP_PORT}/mcp"
DEFAULT_SERVICE_HOST = "127.0.0.1"
DEFAULT_SERVICE_PORT = 8033
DEFAULT_EMBEDDER = "hash"
DEFAULT_TOP_K = 6
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_TIMEOUT_SECONDS = 75
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
THINKING_DISABLED = {"thinking": {"type": "disabled"}}


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class SupportDoc:
    doc_id: str
    source: str
    title: str
    path: Path
    text: str
    sha256: str


@dataclass(frozen=True)
class SupportChunk:
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
class SupportAnswer:
    generated_at: str
    question: str
    user_id: str
    ticket_id: str
    answer: str
    llm_used: bool
    mcp_connected: bool
    mcp_tools: list[str]
    crm_context: dict[str, Any]
    rag_sources: list[dict[str, Any]]
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
    return Path(value or os.getenv("DAY33_SUPPORT_INDEX_DIR", DEFAULT_INDEX_DIR)).resolve()


def store_dir_from_env(value: str | None = None) -> Path:
    load_dotenv()
    return Path(value or os.getenv("DAY33_SUPPORT_STORE_DIR", DEFAULT_STORE_DIR)).resolve()


def kb_dir_from_env(value: str | None = None) -> Path:
    load_dotenv()
    return Path(value or os.getenv("DAY33_SUPPORT_KB_DIR", DEFAULT_KB_DIR)).resolve()


def crm_json_from_env(value: str | None = None) -> Path:
    load_dotenv()
    return Path(value or os.getenv("DAY33_CRM_JSON", DEFAULT_CRM_JSON)).resolve()


def embedder_from_env(value: str | None = None) -> str:
    load_dotenv()
    return (value or os.getenv("DAY33_EMBEDDER", DEFAULT_EMBEDDER)).lower()


def db_path(index_dir: Path) -> Path:
    return index_dir / "support_rag_index.sqlite"


def summary_path(index_dir: Path) -> Path:
    return index_dir / "index_summary.json"


def token_count(text: str) -> int:
    return len(re.findall(r"[A-Za-zА-Яа-яЁё0-9_./:-]+", text))


def collect_documents(root: Path, kb_dir: Path) -> list[SupportDoc]:
    candidates: list[Path] = []
    readme = root / "README.md"
    if readme.exists():
        candidates.append(readme)
    docs_dir = root / "docs"
    if docs_dir.exists():
        candidates.extend(sorted(docs_dir.rglob("*.md")))
    if kb_dir.exists():
        candidates.extend(sorted(kb_dir.rglob("*.md")))

    docs: list[SupportDoc] = []
    seen: set[Path] = set()
    for path in candidates:
        path = path.resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        text = read_text(path)
        if not text or not text.strip():
            continue
        try:
            source = str(path.relative_to(root))
        except ValueError:
            source = str(path)
        doc_id = re.sub(r"[^a-zA-Z0-9]+", "_", source).strip("_").lower()
        docs.append(SupportDoc(doc_id, source, path.name, path, text, sha256_text(text)))
    return docs


def split_words(text: str, max_tokens: int = 230, overlap: int = 35) -> list[str]:
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


def make_chunks(docs: list[SupportDoc]) -> list[SupportChunk]:
    chunks: list[SupportChunk] = []
    for doc in docs:
        chunk_index = 0
        for section, text in markdown_sections(doc.text):
            for part in split_words(text):
                chunk_index += 1
                chunks.append(
                    SupportChunk(
                        chunk_id=f"{doc.doc_id}_{chunk_index:03d}",
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


def build_index(root: Path, index_dir: Path, kb_dir: Path, provider: str) -> dict[str, Any]:
    docs = collect_documents(root, kb_dir)
    if not docs:
        raise FileNotFoundError("No README.md, docs/*.md, or day33 support knowledge files found")
    chunks = make_chunks(docs)
    if not chunks:
        raise RuntimeError("No chunks created for support RAG index")
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
            "kb_dir": str(kb_dir),
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
        raise FileNotFoundError(f"Day33 support index missing: {path}. Run day33_support_assistant.py index")
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_index(root: Path, index_dir: Path, kb_dir: Path, provider: str) -> dict[str, Any]:
    if db_path(index_dir).exists() and summary_path(index_dir).exists():
        return load_summary(index_dir)
    return build_index(root, index_dir, kb_dir, provider)


def search_index(index_dir: Path, query: str, top_k: int) -> list[RetrievedChunk]:
    summary = load_summary(index_dir)
    provider = summary["embedding_provider"]
    dim = int(summary["embedding_dim"])
    embedder = create_embedder("hash" if provider == "local_hashing_v1" else provider, dim)
    query_vector = embedder.embed(query)
    conn = sqlite3.connect(db_path(index_dir))
    try:
        rows = conn.execute("SELECT chunk_id, source, title, section, text, embedding FROM chunks").fetchall()
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


def start_mcp_server(root: Path, store_dir: Path, crm_path: Path, host: str, port: int) -> subprocess.Popen[str]:
    store_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [
            sys.executable,
            "day33_support_mcp_server.py",
            "serve",
            "--host",
            host,
            "--port",
            str(port),
            "--crm-json",
            str(crm_path),
        ],
        cwd=root,
        text=True,
        stdout=(store_dir / "mcp_server.stdout.log").open("w", encoding="utf-8"),
        stderr=(store_dir / "mcp_server.stderr.log").open("w", encoding="utf-8"),
    )


def stop_process(process: subprocess.Popen[str] | None) -> None:
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
    return args.mcp_url or os.getenv("DAY33_MCP_URL", DEFAULT_MCP_URL)


def deepseek_client() -> tuple[OpenAI, str]:
    load_dotenv()
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    model = os.getenv("DAY33_DEEPSEEK_MODEL") or os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    timeout = parse_int(os.getenv("DAY33_DEEPSEEK_TIMEOUT_SECONDS"), DEFAULT_TIMEOUT_SECONDS)
    return OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL, timeout=timeout, max_retries=0), model


def compact_json(data: Any, max_chars: int = 6500) -> str:
    text = to_json(data)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...truncated..."


def build_retrieval_query(question: str, crm_context: dict[str, Any]) -> str:
    parts = [question]
    ticket = crm_context.get("ticket", {}).get("ticket") if isinstance(crm_context.get("ticket"), dict) else None
    user = crm_context.get("user", {}).get("user") if isinstance(crm_context.get("user"), dict) else None
    if ticket:
        parts.extend(
            [
                str(ticket.get("subject", "")),
                str(ticket.get("description", "")),
                " ".join(str(tag) for tag in ticket.get("tags", [])),
            ]
        )
        for event in ticket.get("events", [])[-3:]:
            parts.append(str(event.get("text", "")))
    if user:
        parts.extend([str(user.get("plan", "")), str(user.get("environment", "")), str(user.get("preferences", ""))])
    return " ".join(part for part in parts if part)


def build_prompt(question: str, crm_context: dict[str, Any], chunks: list[RetrievedChunk]) -> list[dict[str, str]]:
    rag_context = "\n\n".join(
        [
            (
                f"[source={chunk.source} section={chunk.section} chunk_id={chunk.chunk_id} score={chunk.score:.4f}]\n"
                f"{chunk.text}"
            )
            for chunk in chunks
        ]
    )
    return [
        {
            "role": "system",
            "content": (
                "Ты AI-ассистент поддержки пользователей продукта AiTgsterBot. "
                "Отвечай по-русски, коротко и практически. "
                "Используй только RAG-документацию и CRM-контекст из MCP. "
                "Не выдумывай статус пользователя, тикеты, настройки и команды. "
                "Если данных не хватает, скажи это и попроси конкретное уточнение. "
                "Всегда показывай источники RAG и какие данные CRM/MCP были учтены."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Вопрос пользователя:\n{question}\n\n"
                f"CRM/MCP context:\n{compact_json(crm_context)}\n\n"
                f"RAG context:\n{rag_context}\n\n"
                "Формат ответа строго:\n"
                "Ответ: ...\n"
                "Учет тикета/пользователя: ...\n"
                "Что проверить: 1) ... 2) ...\n"
                "Источники RAG: source + section + chunk_id\n"
                "MCP данные: tool names / user_id / ticket_id\n"
            ),
        },
    ]


def fallback_answer(
    question: str,
    user_id: str,
    ticket_id: str,
    crm_context: dict[str, Any],
    chunks: list[RetrievedChunk],
    tools: list[str],
    error: str,
    elapsed: float,
) -> SupportAnswer:
    source_lines = "\n".join(
        f"- {chunk.source} / {chunk.section} / {chunk.chunk_id}: {re.sub(r'\\s+', ' ', chunk.text)[:220]}"
        for chunk in chunks
    )
    answer = (
        "Ответ: DeepSeek сейчас недоступен, поэтому показываю проверяемый support-контекст без генерации модели.\n"
        f"Вопрос: {question}\n"
        f"Учет тикета/пользователя: user_id={user_id or '-'}, ticket_id={ticket_id or '-'}.\n"
        "Что проверить: 1) настройки из CRM/MCP; 2) найденные RAG-источники; 3) ошибку API перед повторным запуском.\n"
        f"Источники RAG:\n{source_lines}\n"
        f"MCP данные: tools={', '.join(tools)}."
    )
    return SupportAnswer(
        generated_at=now_iso(),
        question=question,
        user_id=user_id,
        ticket_id=ticket_id,
        answer=answer,
        llm_used=False,
        mcp_connected=bool(tools),
        mcp_tools=tools,
        crm_context=crm_context,
        rag_sources=[asdict(chunk) for chunk in chunks],
        tokens={"prompt": 0, "completion": 0, "total": 0},
        elapsed_seconds=elapsed,
        error=error,
    )


def call_deepseek(
    question: str,
    user_id: str,
    ticket_id: str,
    crm_context: dict[str, Any],
    chunks: list[RetrievedChunk],
    tools: list[str],
    started: float,
) -> SupportAnswer:
    messages = build_prompt(question, crm_context, chunks)
    try:
        client, model = deepseek_client()
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            max_tokens=1000,
            extra_body=THINKING_DISABLED,
        )
        usage = response.usage
        return SupportAnswer(
            generated_at=now_iso(),
            question=question,
            user_id=user_id,
            ticket_id=ticket_id,
            answer=response.choices[0].message.content or "",
            llm_used=True,
            mcp_connected=bool(tools),
            mcp_tools=tools,
            crm_context=crm_context,
            rag_sources=[asdict(chunk) for chunk in chunks],
            tokens={
                "prompt": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion": int(getattr(usage, "completion_tokens", 0) or 0),
                "total": int(getattr(usage, "total_tokens", 0) or 0),
            },
            elapsed_seconds=round(time.perf_counter() - started, 3),
        )
    except (APIConnectionError, APIError, APIStatusError, APITimeoutError, RuntimeError) as exc:
        return fallback_answer(
            question,
            user_id,
            ticket_id,
            crm_context,
            chunks,
            tools,
            str(exc),
            round(time.perf_counter() - started, 3),
        )


def collect_crm_context(url: str, question: str, user_id: str, ticket_id: str, timeout_seconds: float) -> dict[str, Any]:
    context: dict[str, Any] = {}
    if user_id:
        context["user"] = asyncio.run(call_mcp_tool(url, "get_user_profile", {"user_id": user_id}, timeout_seconds))
    if ticket_id:
        context["ticket"] = asyncio.run(call_mcp_tool(url, "get_ticket", {"ticket_id": ticket_id}, timeout_seconds))
    context["ticket_search"] = asyncio.run(
        call_mcp_tool(url, "search_support_tickets", {"query": question, "limit": 5}, timeout_seconds)
    )
    return context


def run_support_answer(args: argparse.Namespace) -> SupportAnswer:
    load_dotenv()
    started = time.perf_counter()
    root = Path(args.project_root).resolve()
    index_dir = index_dir_from_env(args.index_dir)
    store_dir = store_dir_from_env(args.store_dir)
    kb_dir = kb_dir_from_env(args.kb_dir)
    crm_path = crm_json_from_env(args.crm_json)
    provider = embedder_from_env(args.embedder)
    top_k = int(args.top_k)
    timeout_seconds = float(parse_int(os.getenv("DAY33_MCP_TIMEOUT_SECONDS"), 20))

    ensure_index(root, index_dir, kb_dir, provider)
    mcp_url = mcp_url_from_args(args)
    spawned: subprocess.Popen[str] | None = None
    if not args.mcp_url and not args.no_spawn_mcp:
        spawned = start_mcp_server(root, store_dir, crm_path, DEFAULT_MCP_HOST, DEFAULT_MCP_PORT)
    try:
        tools_raw = wait_for_mcp(mcp_url, 15)
        tool_names = [str(tool.get("name")) for tool in tools_raw]
        crm_context = collect_crm_context(mcp_url, args.question, args.user_id or "", args.ticket_id or "", timeout_seconds)
        query = build_retrieval_query(args.question, crm_context)
        chunks = search_index(index_dir, query, top_k)
        result = call_deepseek(args.question, args.user_id or "", args.ticket_id or "", crm_context, chunks, tool_names, started)
        store_dir.mkdir(parents=True, exist_ok=True)
        (store_dir / "last_answer.json").write_text(to_json(asdict(result)), encoding="utf-8")
        return result
    finally:
        stop_process(spawned)


def print_answer(result: SupportAnswer) -> None:
    print("day33_support_answer")
    print(f"mcp_connected={result.mcp_connected}")
    print(f"mcp_tools={', '.join(result.mcp_tools)}")
    print(f"user_id={result.user_id or '-'}")
    print(f"ticket_id={result.ticket_id or '-'}")
    print(f"llm_used={result.llm_used}")
    print(f"tokens={to_json(result.tokens)}")
    if result.error:
        print(f"error={result.error}")
    print("\nanswer:")
    print(result.answer)
    print("\nrag_sources:")
    for source in result.rag_sources:
        print(
            f"- score={source['score']:.4f} source={source['source']} "
            f"section={source['section']} chunk_id={source['chunk_id']}"
        )
    print("\ncrm_context:")
    print(compact_json(result.crm_context, 3000))


def render_report(results: list[SupportAnswer], index_summary: dict[str, Any], tools: list[dict[str, Any]]) -> str:
    lines = [
        "# Day33. AI Support Assistant",
        "",
        "## Summary",
        "",
        f"- generated_at: `{now_iso()}`",
        f"- product: `AiTgsterBot`",
        f"- RAG index: `{index_summary['db_path']}`",
        f"- indexed_sources: `{', '.join(index_summary['sources'])}`",
        f"- chunks: `{index_summary['chunks']}`",
        f"- embedding_provider: `{index_summary['embedding_provider']}`",
        f"- MCP tools: `{', '.join(tool.get('name', '') for tool in tools)}`",
        "",
        "## Architecture",
        "",
        "- RAG: README, project docs, and Day33 support FAQ/troubleshooting markdown files.",
        "- MCP: `day33_support_mcp_server.py` exposes JSON CRM users and tickets.",
        "- LLM: DeepSeek receives user question, RAG chunks, and MCP CRM context.",
        "- Output: answer, ticket/user context, diagnostic steps, RAG sources, MCP data.",
        "",
        "## Results",
        "",
    ]
    for index, result in enumerate(results, start=1):
        lines.extend(
            [
                f"### Scenario {index}",
                "",
                f"- question: `{result.question}`",
                f"- user_id: `{result.user_id}`",
                f"- ticket_id: `{result.ticket_id}`",
                f"- llm_used: `{result.llm_used}`",
                f"- tokens: `{result.tokens}`",
                "",
                result.answer,
                "",
                "| score | source | section | chunk_id |",
                "|---:|---|---|---|",
            ]
        )
        for source in result.rag_sources:
            lines.append(f"| {source['score']:.4f} | {source['source']} | {source['section']} | {source['chunk_id']} |")
        lines.extend(["", "CRM context:", "", "```json", compact_json(result.crm_context, 5000), "```", ""])
    lines.extend(
        [
            "## Check Commands",
            "",
            "```powershell",
            ".\\.venv\\Scripts\\python.exe day33_support_assistant.py index",
            ".\\.venv\\Scripts\\python.exe day33_support_assistant.py mcp-tools",
            ".\\.venv\\Scripts\\python.exe day33_support_assistant.py ask \"Почему бот не отвечает на /start?\" --user-id u1001 --ticket-id t9001",
            ".\\.venv\\Scripts\\python.exe day33_support_assistant.py demo",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def command_index(args: argparse.Namespace) -> int:
    summary = build_index(
        Path(args.project_root).resolve(),
        index_dir_from_env(args.index_dir),
        kb_dir_from_env(args.kb_dir),
        embedder_from_env(args.embedder),
    )
    print(f"indexed_sources={len(summary['sources'])}")
    print(f"chunks={summary['chunks']}")
    print(f"embedding_provider={summary['embedding_provider']}")
    print(f"db_path={summary['db_path']}")
    return 0


def command_mcp_tools(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    store_dir = store_dir_from_env(args.store_dir)
    crm_path = crm_json_from_env(args.crm_json)
    url = mcp_url_from_args(args)
    spawned = None
    if not args.mcp_url and not args.no_spawn_mcp:
        spawned = start_mcp_server(root, store_dir, crm_path, DEFAULT_MCP_HOST, DEFAULT_MCP_PORT)
    try:
        tools = wait_for_mcp(url, 15)
        print(f"mcp_url={url}")
        print("connected=True")
        print(f"tools_count={len(tools)}")
        for tool in tools:
            print(f"- {tool['name']}: {tool['description']}")
        return 0
    finally:
        stop_process(spawned)


def command_ask(args: argparse.Namespace) -> int:
    result = run_support_answer(args)
    print_answer(result)
    return 0 if result.mcp_connected and result.rag_sources else 2


def command_demo(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    index_summary = build_index(root, index_dir_from_env(args.index_dir), kb_dir_from_env(args.kb_dir), embedder_from_env(args.embedder))
    store_dir = store_dir_from_env(args.store_dir)
    crm_path = crm_json_from_env(args.crm_json)
    spawned = start_mcp_server(root, store_dir, crm_path, DEFAULT_MCP_HOST, DEFAULT_MCP_PORT)
    try:
        tools = wait_for_mcp(DEFAULT_MCP_URL, 15)
    finally:
        stop_process(spawned)

    scenarios = [
        {
            "question": "Почему бот не отвечает на /start после запуска?",
            "user_id": "u1001",
            "ticket_id": "t9001",
        },
        {
            "question": "Почему /day10 выглядит как быстрый локальный ответ, а не DeepSeek-сравнение?",
            "user_id": "u1002",
            "ticket_id": "t9002",
        },
        {
            "question": "Почему после перезапуска не восстановилась история диалога?",
            "user_id": "u1003",
            "ticket_id": "t9003",
        },
    ]
    results: list[SupportAnswer] = []
    for scenario in scenarios:
        scenario_args = argparse.Namespace(**vars(args))
        scenario_args.question = scenario["question"]
        scenario_args.user_id = scenario["user_id"]
        scenario_args.ticket_id = scenario["ticket_id"]
        scenario_args.mcp_url = None
        scenario_args.no_spawn_mcp = False
        results.append(run_support_answer(scenario_args))
        print(f"scenario={len(results)} user={scenario['user_id']} ticket={scenario['ticket_id']} llm_used={results[-1].llm_used}")

    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "last_demo.json").write_text(to_json([asdict(result) for result in results]), encoding="utf-8")
    report = render_report(results, index_summary, tools)
    Path(DEFAULT_REPORT_PATH).write_text(report, encoding="utf-8")
    print(f"report_saved={Path(DEFAULT_REPORT_PATH).resolve()}")
    return 0 if all(result.mcp_connected and result.rag_sources for result in results) else 2


def command_show_crm(args: argparse.Namespace) -> int:
    crm_path = crm_json_from_env(args.crm_json)
    data = json.loads(crm_path.read_text(encoding="utf-8"))
    print(to_json(data))
    return 0


def command_serve(args: argparse.Namespace) -> int:
    try:
        from fastapi import Body, FastAPI, HTTPException
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("Install fastapi and uvicorn to use serve command") from exc

    app = FastAPI(title="Day33 Support Assistant", version="1.0")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "service": "day33-support-assistant",
            "rag_index": str(index_dir_from_env(args.index_dir)),
            "crm_json": str(crm_json_from_env(args.crm_json)),
        }

    @app.post("/support/answer")
    def support_answer(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        question = str(payload.get("question", "")).strip()
        if not question:
            raise HTTPException(status_code=400, detail="question is required")
        request_args = argparse.Namespace(**vars(args))
        request_args.question = question
        request_args.user_id = str(payload.get("user_id", ""))
        request_args.ticket_id = str(payload.get("ticket_id", ""))
        result = run_support_answer(request_args)
        return asdict(result)

    print(f"starting support service on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--index-dir")
    parser.add_argument("--store-dir")
    parser.add_argument("--kb-dir")
    parser.add_argument("--crm-json")
    parser.add_argument("--embedder")


def add_mcp_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mcp-url")
    parser.add_argument("--no-spawn-mcp", action="store_true")


def main() -> None:
    parser = argparse.ArgumentParser(description="Day33 support assistant with RAG + JSON CRM MCP.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index")
    add_common_args(index_parser)
    index_parser.set_defaults(func=command_index)

    tools_parser = subparsers.add_parser("mcp-tools")
    add_common_args(tools_parser)
    add_mcp_args(tools_parser)
    tools_parser.set_defaults(func=command_mcp_tools)

    ask_parser = subparsers.add_parser("ask")
    ask_parser.add_argument("question")
    ask_parser.add_argument("--user-id", default="")
    ask_parser.add_argument("--ticket-id", default="")
    ask_parser.add_argument("--top-k", type=int, default=parse_int(os.getenv("DAY33_TOP_K"), DEFAULT_TOP_K))
    add_common_args(ask_parser)
    add_mcp_args(ask_parser)
    ask_parser.set_defaults(func=command_ask)

    demo_parser = subparsers.add_parser("demo")
    demo_parser.add_argument("--top-k", type=int, default=parse_int(os.getenv("DAY33_TOP_K"), DEFAULT_TOP_K))
    add_common_args(demo_parser)
    add_mcp_args(demo_parser)
    demo_parser.set_defaults(func=command_demo)

    crm_parser = subparsers.add_parser("show-crm")
    add_common_args(crm_parser)
    crm_parser.set_defaults(func=command_show_crm)

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--host", default=DEFAULT_SERVICE_HOST)
    serve_parser.add_argument("--port", type=int, default=DEFAULT_SERVICE_PORT)
    serve_parser.add_argument("--top-k", type=int, default=parse_int(os.getenv("DAY33_TOP_K"), DEFAULT_TOP_K))
    serve_parser.add_argument("--user-id", default="")
    serve_parser.add_argument("--ticket-id", default="")
    serve_parser.add_argument("--question", default="")
    add_common_args(serve_parser)
    add_mcp_args(serve_parser)
    serve_parser.set_defaults(func=command_serve)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
