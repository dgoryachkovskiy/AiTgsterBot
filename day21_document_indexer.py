import argparse
import array
import hashlib
import json
import math
import os
import re
import sqlite3
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from dotenv import load_dotenv


DEFAULT_SOURCE_DIR = r"C:\Users\pospi\AndroidStudioProjects\AstroTarot"
DEFAULT_INDEX_DIR = "day21_index_store"
DEFAULT_DB_NAME = "astro_tarot_rag_index.sqlite"
DEFAULT_REPORT_PATH = "DAY21_DOCUMENT_INDEXING_REPORT.md"
DEFAULT_HASH_EMBEDDING_DIM = 384
DEFAULT_EMBEDDING_PROVIDER = "ollama"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "nomic-embed-text"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 120
FIXED_CHUNK_TOKENS = 240
FIXED_CHUNK_OVERLAP = 40
STRUCTURE_MAX_TOKENS = 360
TEXT_EXTENSIONS = {
    ".gradle",
    ".java",
    ".json",
    ".kt",
    ".kts",
    ".md",
    ".properties",
    ".toml",
    ".xml",
    ".yaml",
    ".yml",
}
EXCLUDED_DIRS = {
    ".git",
    ".gradle",
    ".idea",
    ".kotlin",
    "build",
    "captures",
    "generated",
    "out",
}
EXCLUDED_FILES = {
    "local.properties",
}
QUERIES = [
    "email login verification code",
    "tarot card reading screen",
    "backend authentication endpoint",
    "Jetpack Compose navigation",
]


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class Document:
    doc_id: str
    path: Path
    relative_path: str
    title: str
    text: str
    sha256: str


@dataclass(frozen=True)
class TokenSpan:
    token: str
    start: int
    end: int


@dataclass(frozen=True)
class Chunk:
    strategy: str
    chunk_id: str
    doc_id: str
    source: str
    title: str
    section: str
    chunk_index: int
    start_line: int
    end_line: int
    start_char: int
    end_char: int
    text: str
    token_count: int


@dataclass(frozen=True)
class SearchResult:
    rank: int
    score: float
    strategy: str
    chunk_id: str
    source: str
    section: str
    title: str
    preview: str


def utc_now() -> str:
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


def load_source_dir(args: argparse.Namespace) -> Path:
    load_dotenv()
    return Path(args.source or os.getenv("DAY21_SOURCE_DIR", DEFAULT_SOURCE_DIR)).resolve()


def load_index_dir(args: argparse.Namespace) -> Path:
    load_dotenv()
    return Path(args.index_dir or os.getenv("DAY21_INDEX_DIR", DEFAULT_INDEX_DIR)).resolve()


def hash_embedding_dim_from_env() -> int:
    load_dotenv()
    return parse_int(os.getenv("DAY21_HASH_EMBEDDING_DIM"), DEFAULT_HASH_EMBEDDING_DIM)


def embedding_provider_from_env(default: str = DEFAULT_EMBEDDING_PROVIDER) -> str:
    load_dotenv()
    return os.getenv("DAY21_EMBEDDER", default).lower()


def ollama_base_url_from_env() -> str:
    load_dotenv()
    return os.getenv("DAY21_OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL).rstrip("/")


def ollama_model_from_env() -> str:
    load_dotenv()
    return os.getenv("DAY21_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)


def ollama_timeout_from_env() -> int:
    load_dotenv()
    return parse_int(os.getenv("DAY21_OLLAMA_TIMEOUT_SECONDS"), DEFAULT_OLLAMA_TIMEOUT_SECONDS)


def db_path(index_dir: Path) -> Path:
    return index_dir / DEFAULT_DB_NAME


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def should_skip(path: Path) -> bool:
    if path.name in EXCLUDED_FILES:
        return True
    return any(part in EXCLUDED_DIRS for part in path.parts)


def is_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXTENSIONS and not should_skip(path)


def read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:4096]:
        return None
    for encoding in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def collect_documents(source_dir: Path) -> list[Document]:
    docs = []
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file() or not is_text_file(path):
            continue
        text = read_text(path)
        if not text or not text.strip():
            continue
        relative = str(path.relative_to(source_dir))
        doc_id = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:16]
        docs.append(Document(doc_id, path, relative, path.name, text, sha256_text(text)))
    return docs


def tokenize_with_spans(text: str) -> list[TokenSpan]:
    spans = []
    for match in re.finditer(r"[A-Za-zА-Яа-яЁё0-9_./:-]+", text):
        token = match.group(0).lower()
        if token:
            spans.append(TokenSpan(token, match.start(), match.end()))
    return spans


def line_number(text: str, char_index: int) -> int:
    return text.count("\n", 0, max(0, min(char_index, len(text)))) + 1


def section_for_char(text: str, char_index: int) -> str:
    prefix = text[:char_index]
    lines = prefix.splitlines()
    for line in reversed(lines[-80:]):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()[:160] or "markdown"
        if re.match(r"(public |private |internal |protected )?(data )?(class|object|interface|enum class|fun)\s+", stripped):
            return stripped[:160]
        if re.match(r"<[A-Za-z][A-Za-z0-9_.:-]+", stripped):
            return stripped[:160]
    return "file"


def build_chunk(
    strategy: str,
    document: Document,
    chunk_index: int,
    start_char: int,
    end_char: int,
    token_count: int,
    section: str | None = None,
) -> Chunk:
    text = document.text[start_char:end_char].strip()
    chunk_id = f"{strategy}:{document.doc_id}:{chunk_index:05d}"
    return Chunk(
        strategy=strategy,
        chunk_id=chunk_id,
        doc_id=document.doc_id,
        source=document.relative_path,
        title=document.title,
        section=section or section_for_char(document.text, start_char),
        chunk_index=chunk_index,
        start_line=line_number(document.text, start_char),
        end_line=line_number(document.text, end_char),
        start_char=start_char,
        end_char=end_char,
        text=text,
        token_count=token_count,
    )


def fixed_chunks(document: Document, chunk_tokens: int = FIXED_CHUNK_TOKENS, overlap: int = FIXED_CHUNK_OVERLAP) -> list[Chunk]:
    spans = tokenize_with_spans(document.text)
    if not spans:
        return []
    chunks = []
    step = max(1, chunk_tokens - overlap)
    index = 0
    for start_token in range(0, len(spans), step):
        window = spans[start_token : start_token + chunk_tokens]
        if not window:
            break
        chunks.append(build_chunk("fixed", document, index, window[0].start, window[-1].end, len(window)))
        index += 1
        if start_token + chunk_tokens >= len(spans):
            break
    return chunks


def structure_boundaries(document: Document) -> list[tuple[int, str]]:
    boundaries = [(0, "file")]
    cursor = 0
    for line in document.text.splitlines(keepends=True):
        stripped = line.strip()
        section = ""
        if stripped.startswith("#"):
            section = stripped.lstrip("#").strip() or "markdown"
        elif re.match(r"(public |private |internal |protected )?(data )?(class|object|interface|enum class)\s+", stripped):
            section = stripped
        elif re.match(r"(public |private |internal |protected |override |suspend )*fun\s+", stripped):
            section = stripped
        elif stripped.startswith("<") and re.match(r"<[A-Za-z][A-Za-z0-9_.:-]+", stripped):
            section = stripped[:160]
        if section and cursor != boundaries[-1][0]:
            boundaries.append((cursor, section[:160]))
        cursor += len(line)
    return boundaries


def split_large_section(document: Document, section: str, start: int, end: int, start_index: int) -> list[Chunk]:
    text = document.text[start:end]
    spans = tokenize_with_spans(text)
    if not spans:
        return []
    chunks = []
    step = STRUCTURE_MAX_TOKENS
    for offset, token_start in enumerate(range(0, len(spans), step)):
        window = spans[token_start : token_start + STRUCTURE_MAX_TOKENS]
        if not window:
            continue
        chunks.append(
            build_chunk(
                "structure",
                document,
                start_index + offset,
                start + window[0].start,
                start + window[-1].end,
                len(window),
                section=section,
            )
        )
    return chunks


def structure_chunks(document: Document) -> list[Chunk]:
    boundaries = structure_boundaries(document)
    ranges = []
    for index, (start, section) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(document.text)
        if document.text[start:end].strip():
            ranges.append((start, end, section))
    chunks = []
    chunk_index = 0
    for start, end, section in ranges:
        token_count = len(tokenize_with_spans(document.text[start:end]))
        if token_count == 0:
            continue
        if token_count <= STRUCTURE_MAX_TOKENS:
            chunks.append(build_chunk("structure", document, chunk_index, start, end, token_count, section=section))
            chunk_index += 1
        else:
            split = split_large_section(document, section, start, end, chunk_index)
            chunks.extend(split)
            chunk_index += len(split)
    return chunks


def normalize_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return vector
    return [value / norm for value in vector]


class HashingEmbedder:
    provider = "local_hashing_v1"
    model = "hashing"
    base_url = ""

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        tokens = [span.token for span in tokenize_with_spans(text)]
        for token in tokens:
            self._add_feature(vector, token, 1.0)
            if "_" in token:
                for part in token.split("_"):
                    if part:
                        self._add_feature(vector, part, 0.5)
            if len(token) >= 5:
                for i in range(0, len(token) - 2):
                    self._add_feature(vector, f"tri:{token[i:i+3]}", 0.15)
        return normalize_vector(vector)

    def _add_feature(self, vector: list[float], feature: str, weight: float) -> None:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        number = int.from_bytes(digest, "little")
        index = number % self.dim
        sign = 1.0 if (number >> 63) == 0 else -1.0
        vector[index] += sign * weight


class OllamaEmbedder:
    provider = "ollama"

    def __init__(self, base_url: str, model: str, timeout_seconds: int, expected_dim: int | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.dim = expected_dim

    def embed(self, text: str) -> list[float]:
        payload = json.dumps({"model": self.model, "prompt": text}, ensure_ascii=False).encode("utf-8")
        request = urlrequest.Request(
            f"{self.base_url}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlrequest.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urlerror.URLError as error:
            raise RuntimeError(
                f"Ollama is not available at {self.base_url}. Run: ollama serve; ollama pull {self.model}"
            ) from error
        embedding = data.get("embedding")
        if not isinstance(embedding, list) or not embedding:
            raise RuntimeError(f"Ollama returned no embedding for model {self.model}")
        vector = [float(value) for value in embedding]
        if self.dim is None:
            self.dim = len(vector)
        if len(vector) != self.dim:
            raise RuntimeError(f"Ollama embedding dim changed: expected {self.dim}, got {len(vector)}")
        return normalize_vector(vector)


def create_embedder(provider: str, expected_dim: int | None = None) -> HashingEmbedder | OllamaEmbedder:
    provider = provider.lower()
    if provider == "hash":
        return HashingEmbedder(expected_dim or DEFAULT_HASH_EMBEDDING_DIM)
    if provider == "ollama":
        return OllamaEmbedder(ollama_base_url_from_env(), ollama_model_from_env(), ollama_timeout_from_env(), expected_dim)
    raise ValueError("embedder must be ollama or hash")


def vector_to_blob(vector: list[float]) -> bytes:
    values = array.array("f", vector)
    return values.tobytes()


def blob_to_vector(blob: bytes) -> list[float]:
    values = array.array("f")
    values.frombytes(blob)
    return values.tolist()


def cosine_from_normalized(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS chunks;
        DROP TABLE IF EXISTS documents;
        DROP TABLE IF EXISTS index_meta;

        CREATE TABLE documents (
            doc_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            path TEXT NOT NULL,
            file_ext TEXT NOT NULL,
            size_chars INTEGER NOT NULL,
            line_count INTEGER NOT NULL,
            sha256 TEXT NOT NULL
        );

        CREATE TABLE chunks (
            strategy TEXT NOT NULL,
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            section TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            start_char INTEGER NOT NULL,
            end_char INTEGER NOT NULL,
            token_count INTEGER NOT NULL,
            text TEXT NOT NULL,
            embedding BLOB NOT NULL,
            embedding_dim INTEGER NOT NULL,
            embedding_provider TEXT NOT NULL,
            embedding_model TEXT NOT NULL,
            FOREIGN KEY(doc_id) REFERENCES documents(doc_id)
        );

        CREATE INDEX idx_chunks_strategy ON chunks(strategy);
        CREATE INDEX idx_chunks_source ON chunks(source);

        CREATE TABLE index_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def insert_documents(conn: sqlite3.Connection, docs: list[Document]) -> None:
    conn.executemany(
        """
        INSERT INTO documents(doc_id, source, title, path, file_ext, size_chars, line_count, sha256)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                doc.doc_id,
                doc.relative_path,
                doc.title,
                str(doc.path),
                doc.path.suffix.lower(),
                len(doc.text),
                doc.text.count("\n") + 1,
                doc.sha256,
            )
            for doc in docs
        ],
    )


def insert_chunks(conn: sqlite3.Connection, chunks: list[Chunk], embedder: HashingEmbedder | OllamaEmbedder) -> int:
    rows = []
    for index, chunk in enumerate(chunks, start=1):
        vector = embedder.embed(chunk.text)
        dim = len(vector)
        if index == 1 or index % 100 == 0 or index == len(chunks):
            print(f"embedded_chunks={index}/{len(chunks)} provider={embedder.provider} dim={dim}", flush=True)
        rows.append(
            (
                chunk.strategy,
                chunk.chunk_id,
                chunk.doc_id,
                chunk.source,
                chunk.title,
                chunk.section,
                chunk.chunk_index,
                chunk.start_line,
                chunk.end_line,
                chunk.start_char,
                chunk.end_char,
                chunk.token_count,
                chunk.text,
                vector_to_blob(vector),
                dim,
                embedder.provider,
                embedder.model,
            )
        )
    conn.executemany(
        """
        INSERT INTO chunks(
            strategy, chunk_id, doc_id, source, title, section, chunk_index,
            start_line, end_line, start_char, end_char, token_count, text,
            embedding, embedding_dim, embedding_provider, embedding_model
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return int(getattr(embedder, "dim", 0) or 0)


def upsert_meta(conn: sqlite3.Connection, data: dict[str, Any]) -> None:
    conn.executemany(
        "INSERT INTO index_meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [(key, json.dumps(value, ensure_ascii=False)) for key, value in data.items()],
    )


def percentile(values: list[int], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percent)))
    return float(ordered[index])


def chunk_stats(chunks: list[Chunk]) -> dict[str, Any]:
    token_counts = [chunk.token_count for chunk in chunks]
    sources = {chunk.source for chunk in chunks}
    return {
        "chunks": len(chunks),
        "sources": len(sources),
        "avg_tokens": round(statistics.mean(token_counts), 2) if token_counts else 0,
        "median_tokens": round(statistics.median(token_counts), 2) if token_counts else 0,
        "p95_tokens": round(percentile(token_counts, 0.95), 2),
        "min_tokens": min(token_counts) if token_counts else 0,
        "max_tokens": max(token_counts) if token_counts else 0,
    }


def build_summary(
    source_dir: Path,
    docs: list[Document],
    fixed: list[Chunk],
    structure: list[Chunk],
    db: Path,
    embedder: HashingEmbedder | OllamaEmbedder,
    embedding_dim: int,
) -> dict[str, Any]:
    total_chars = sum(len(doc.text) for doc in docs)
    total_lines = sum(doc.text.count("\n") + 1 for doc in docs)
    return {
        "generated_at": utc_now(),
        "source_dir": str(source_dir),
        "db_path": str(db),
        "embedding_provider": embedder.provider,
        "embedding_model": embedder.model,
        "embedding_base_url": embedder.base_url,
        "embedding_dim": embedding_dim,
        "documents": len(docs),
        "total_chars": total_chars,
        "total_lines": total_lines,
        "estimated_pages_1800_chars": round(total_chars / 1800, 2),
        "strategies": {
            "fixed": {
                "description": f"Fixed token windows: {FIXED_CHUNK_TOKENS} tokens, overlap {FIXED_CHUNK_OVERLAP}.",
                **chunk_stats(fixed),
            },
            "structure": {
                "description": f"Split by headings/classes/functions/XML nodes, max {STRUCTURE_MAX_TOKENS} tokens per section.",
                **chunk_stats(structure),
            },
        },
    }


def search_index(index_dir: Path, strategy: str, query: str, top_k: int, provider: str, dim: int) -> list[SearchResult]:
    embedder = create_embedder("hash" if provider == "local_hashing_v1" else provider, dim)
    query_vector = embedder.embed(query)
    conn = sqlite3.connect(db_path(index_dir))
    try:
        rows = conn.execute(
            """
            SELECT strategy, chunk_id, source, section, title, text, embedding
            FROM chunks
            WHERE strategy = ?
            """,
            (strategy,),
        ).fetchall()
    finally:
        conn.close()
    scored = []
    for row in rows:
        score = cosine_from_normalized(query_vector, blob_to_vector(row[6]))
        preview = re.sub(r"\s+", " ", row[5]).strip()[:220]
        scored.append(SearchResult(0, score, row[0], row[1], row[2], row[3], row[4], preview))
    scored.sort(key=lambda item: item.score, reverse=True)
    return [
        SearchResult(index, item.score, item.strategy, item.chunk_id, item.source, item.section, item.title, item.preview)
        for index, item in enumerate(scored[:top_k], start=1)
    ]


def compare_search(index_dir: Path, provider: str, dim: int) -> dict[str, Any]:
    comparison = {}
    for query in QUERIES:
        comparison[query] = {}
        for strategy in ("fixed", "structure"):
            results = search_index(index_dir, strategy, query, 5, provider, dim)
            comparison[query][strategy] = {
                "top_sources": [result.source for result in results],
                "top_sections": [result.section for result in results],
                "avg_top5_score": round(statistics.mean([result.score for result in results]), 4) if results else 0,
                "distinct_files": len({result.source for result in results}),
            }
    return comparison


def build_report(summary: dict[str, Any], comparison: dict[str, Any]) -> str:
    fixed = summary["strategies"]["fixed"]
    structure = summary["strategies"]["structure"]
    return "\n".join(
        [
            "# Day 21. Document Indexing",
            "",
            "## Summary",
            "",
            f"- source: `{summary['source_dir']}`",
            f"- documents: `{summary['documents']}`",
            f"- estimated_pages: `{summary['estimated_pages_1800_chars']}`",
            f"- index: `{summary['db_path']}`",
            f"- embedding_provider: `{summary['embedding_provider']}`",
            f"- embedding_model: `{summary['embedding_model']}`",
            f"- embedding_base_url: `{summary['embedding_base_url']}`",
            f"- embedding_dim: `{summary['embedding_dim']}`",
            "",
            "## Chunking Strategies",
            "",
            "| strategy | chunks | avg tokens | p95 tokens | sources | description |",
            "|---|---:|---:|---:|---:|---|",
            f"| fixed | {fixed['chunks']} | {fixed['avg_tokens']} | {fixed['p95_tokens']} | {fixed['sources']} | {fixed['description']} |",
            f"| structure | {structure['chunks']} | {structure['avg_tokens']} | {structure['p95_tokens']} | {structure['sources']} | {structure['description']} |",
            "",
            "## Retrieval Comparison",
            "",
            "```json",
            to_json(comparison),
            "```",
            "",
            "## What Is Stored Per Chunk",
            "",
            "- `source`: relative file path",
            "- `title`: file name",
            "- `section`: heading/class/function/XML node/file",
            "- `chunk_id`: stable strategy/doc/index id",
            "- `text`: chunk text",
            "- `embedding`: normalized float32 vector blob",
            "",
            "## Check Commands",
            "",
            "```powershell",
            "ollama serve",
            "ollama pull nomic-embed-text",
            ".\\.venv\\Scripts\\python.exe day21_document_indexer.py check-ollama",
            ".\\.venv\\Scripts\\python.exe day21_document_indexer.py build --embedder ollama",
            ".\\.venv\\Scripts\\python.exe day21_document_indexer.py stats",
            ".\\.venv\\Scripts\\python.exe day21_document_indexer.py compare",
            ".\\.venv\\Scripts\\python.exe day21_document_indexer.py search \"email login verification code\" --strategy fixed",
            ".\\.venv\\Scripts\\python.exe day21_document_indexer.py search \"email login verification code\" --strategy structure",
            "```",
        ]
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_json(payload), encoding="utf-8")


def build_index(args: argparse.Namespace) -> dict[str, Any]:
    source_dir = load_source_dir(args)
    index_dir = load_index_dir(args)
    if not source_dir.exists():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")
    docs = collect_documents(source_dir)
    fixed = [chunk for doc in docs for chunk in fixed_chunks(doc)]
    structure = [chunk for doc in docs for chunk in structure_chunks(doc)]
    provider = args.embedder or embedding_provider_from_env()
    embedder = create_embedder(provider, hash_embedding_dim_from_env() if provider == "hash" else None)
    db = db_path(index_dir)
    conn = connect_db(db)
    try:
        init_db(conn)
        insert_documents(conn, docs)
        dim = insert_chunks(conn, fixed + structure, embedder)
        summary = build_summary(source_dir, docs, fixed, structure, db, embedder, dim)
        conn.commit()
        comparison = compare_search(index_dir, summary["embedding_provider"], dim)
        upsert_meta(conn, {"summary": summary, "comparison": comparison})
        conn.commit()
    finally:
        conn.close()
    write_json(index_dir / "index_summary.json", summary)
    write_json(index_dir / "chunking_comparison.json", comparison)
    Path(args.report).write_text(build_report(summary, comparison), encoding="utf-8")
    return {"summary": summary, "comparison": comparison, "report": str(Path(args.report).resolve())}


def load_summary(index_dir: Path) -> dict[str, Any]:
    path = index_dir / "index_summary.json"
    if not path.exists():
        raise FileNotFoundError(f"Index summary not found: {path}. Run build first.")
    return json.loads(path.read_text(encoding="utf-8"))


def print_summary(summary: dict[str, Any]) -> None:
    print(f"source_dir={summary['source_dir']}")
    print(f"db_path={summary['db_path']}")
    print(f"documents={summary['documents']}")
    print(f"estimated_pages={summary['estimated_pages_1800_chars']}")
    print(f"embedding_provider={summary['embedding_provider']}")
    print(f"embedding_model={summary.get('embedding_model', '')}")
    print(f"embedding_base_url={summary.get('embedding_base_url', '')}")
    print(f"embedding_dim={summary['embedding_dim']}")
    for strategy, stats in summary["strategies"].items():
        print(
            f"strategy={strategy} chunks={stats['chunks']} avg_tokens={stats['avg_tokens']} "
            f"p95_tokens={stats['p95_tokens']} sources={stats['sources']}"
        )


def command_build(args: argparse.Namespace) -> int:
    payload = build_index(args)
    print_summary(payload["summary"])
    print(f"comparison_saved={load_index_dir(args) / 'chunking_comparison.json'}")
    print(f"report_saved={payload['report']}")
    return 0


def command_stats(args: argparse.Namespace) -> int:
    print_summary(load_summary(load_index_dir(args)))
    return 0


def command_compare(args: argparse.Namespace) -> int:
    index_dir = load_index_dir(args)
    path = index_dir / "chunking_comparison.json"
    if not path.exists():
        raise FileNotFoundError(f"Comparison not found: {path}. Run build first.")
    comparison = json.loads(path.read_text(encoding="utf-8"))
    for query, strategies in comparison.items():
        print(f"query={query}")
        for strategy, data in strategies.items():
            print(
                f"  strategy={strategy} avg_top5_score={data['avg_top5_score']} "
                f"distinct_files={data['distinct_files']} top_source={data['top_sources'][0] if data['top_sources'] else ''}"
            )
    return 0


def command_search(args: argparse.Namespace) -> int:
    summary = load_summary(load_index_dir(args))
    results = search_index(
        load_index_dir(args),
        args.strategy,
        args.query,
        args.top_k,
        summary["embedding_provider"],
        summary["embedding_dim"],
    )
    print(f"query={args.query}")
    print(f"strategy={args.strategy}")
    print(f"results={len(results)}")
    for result in results:
        print(f"{result.rank}. score={result.score:.4f} source={result.source} section={result.section}")
        print(f"   chunk_id={result.chunk_id}")
        print(f"   preview={result.preview}")
    return 0


def command_check_ollama(args: argparse.Namespace) -> int:
    del args
    embedder = OllamaEmbedder(ollama_base_url_from_env(), ollama_model_from_env(), ollama_timeout_from_env())
    try:
        vector = embedder.embed("AstroTarot email verification code")
    except RuntimeError:
        print("ollama_connected=False")
        print(f"ollama_url={embedder.base_url}")
        print(f"ollama_model={embedder.model}")
        print("fix:")
        print("  ollama serve")
        print(f"  ollama pull {embedder.model}")
        return 2
    print("ollama_connected=True")
    print(f"ollama_url={embedder.base_url}")
    print(f"ollama_model={embedder.model}")
    print(f"embedding_dim={len(vector)}")
    return 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", default=None, help="Source document/code directory")
    parser.add_argument("--index-dir", default=None, help="Output index directory")


def main() -> None:
    parser = argparse.ArgumentParser(description="Day 21 local document indexing with chunking and embeddings.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check-ollama", help="check Ollama embeddings endpoint")

    build_parser = subparsers.add_parser("build", help="build SQLite vector index")
    add_common_args(build_parser)
    build_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    build_parser.add_argument("--embedder", choices=["ollama", "hash"], default=None)

    stats_parser = subparsers.add_parser("stats", help="print index stats")
    add_common_args(stats_parser)

    compare_parser = subparsers.add_parser("compare", help="print fixed vs structure retrieval comparison")
    add_common_args(compare_parser)

    search_parser = subparsers.add_parser("search", help="search local vector index")
    add_common_args(search_parser)
    search_parser.add_argument("query")
    search_parser.add_argument("--strategy", choices=["fixed", "structure"], default="fixed")
    search_parser.add_argument("--top-k", type=int, default=5)

    args = parser.parse_args()
    if args.command == "check-ollama":
        raise SystemExit(command_check_ollama(args))
    if args.command == "build":
        raise SystemExit(command_build(args))
    if args.command == "stats":
        raise SystemExit(command_stats(args))
    if args.command == "compare":
        raise SystemExit(command_compare(args))
    if args.command == "search":
        raise SystemExit(command_search(args))


if __name__ == "__main__":
    main()
