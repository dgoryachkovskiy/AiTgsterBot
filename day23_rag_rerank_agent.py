import argparse
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI

from day21_document_indexer import blob_to_vector, cosine_from_normalized, create_embedder, db_path, load_summary
from day22_rag_agent import (
    CONTROL_QUESTIONS,
    ControlQuestion,
    LlmResult,
    RetrievedChunk,
    build_rag_context,
    source_hits,
    term_hits,
)


DEFAULT_INDEX_DIR = "day21_index_store"
DEFAULT_STORE_DIR = "day23_rag_store"
DEFAULT_REPORT_PATH = "DAY23_RERANKING_FILTERING_REPORT.md"
DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
THINKING_DISABLED = {"thinking": {"type": "disabled"}}
VALID_MODES = ("baseline", "filtered", "rewrite_rerank")
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_./:-]{2,}")
STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "как",
    "какой",
    "какая",
    "какие",
    "где",
    "что",
    "для",
    "при",
    "или",
    "это",
    "есть",
    "используется",
    "показывается",
}


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class RewriteResult:
    query: str
    keywords: list[str]
    used_deepseek: bool
    fallback_used: bool
    error: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    elapsed_seconds: float


@dataclass(frozen=True)
class Candidate:
    chunk: RetrievedChunk
    vector_score: float
    keyword_score: float = 0.0
    metadata_score: float = 0.0
    rerank_score: float = 0.0


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def parse_float(value: str | None, default: float) -> float:
    if not value:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def parse_int(value: str | None, default: int) -> int:
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def load_index_dir(args: argparse.Namespace) -> Path:
    load_dotenv()
    return Path(args.index_dir or os.getenv("DAY23_RAG_INDEX_DIR", DEFAULT_INDEX_DIR)).resolve()


def load_store_dir() -> Path:
    load_dotenv()
    return Path(os.getenv("DAY23_RAG_STORE_DIR", DEFAULT_STORE_DIR)).resolve()


def model_to_provider(provider: str) -> str:
    return "hash" if provider == "local_hashing_v1" else provider


def parse_modes(mode: str) -> list[str]:
    if mode == "all":
        return list(VALID_MODES)
    if mode not in VALID_MODES:
        raise ValueError(f"Unknown mode: {mode}")
    return [mode]


def parse_thresholds(raw: str) -> list[float]:
    values: list[float] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        values.append(float(item))
    if not values:
        raise ValueError("At least one threshold is required")
    return values


def normalize_terms(text: str) -> list[str]:
    terms = []
    seen = set()
    for match in TOKEN_RE.finditer(text.lower()):
        term = match.group(0).strip(".,;:()[]{}\"'")
        if len(term) < 2 or term in STOP_WORDS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def overlap_score(query_text: str, target_text: str, limit: int = 12) -> float:
    query_terms = normalize_terms(query_text)[:limit]
    if not query_terms:
        return 0.0
    target_terms = set(normalize_terms(target_text))
    hits = 0
    for term in query_terms:
        if term in target_terms:
            hits += 1
            continue
        if len(term) >= 5 and any(term in target for target in target_terms):
            hits += 1
    return round(min(1.0, hits / min(len(query_terms), limit)), 4)


def retrieve_candidates(index_dir: Path, query: str, strategy: str, top_k: int) -> list[Candidate]:
    summary = load_summary(index_dir)
    provider = model_to_provider(str(summary["embedding_provider"]))
    embedder = create_embedder(provider, int(summary["embedding_dim"]))
    query_vector = embedder.embed(query)
    conn = sqlite3.connect(db_path(index_dir))
    try:
        rows = conn.execute(
            """
            SELECT strategy, chunk_id, source, title, section, text, embedding
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
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)

    candidates: list[Candidate] = []
    for rank, (score, row) in enumerate(scored[:top_k], start=1):
        chunk = RetrievedChunk(
            rank=rank,
            score=score,
            strategy=row[0],
            chunk_id=row[1],
            source=row[2],
            title=row[3],
            section=row[4],
            text=row[5],
        )
        candidates.append(Candidate(chunk=chunk, vector_score=score, rerank_score=score))
    return candidates


def filter_candidates(
    candidates: list[Candidate],
    threshold: float,
    min_keep: int,
) -> tuple[list[Candidate], bool]:
    kept = [candidate for candidate in candidates if candidate.vector_score >= threshold]
    fallback_used = False
    if len(kept) < min_keep and candidates:
        kept = candidates[: min(min_keep, len(candidates))]
        fallback_used = True
    return kept, fallback_used


def rerank_candidates(candidates: list[Candidate], query_text: str) -> list[Candidate]:
    reranked = []
    for candidate in candidates:
        chunk = candidate.chunk
        keyword_score = overlap_score(query_text, chunk.text)
        metadata_score = overlap_score(query_text, f"{chunk.source} {chunk.title} {chunk.section}", limit=8)
        rerank_score = round((0.70 * candidate.vector_score) + (0.20 * keyword_score) + (0.10 * metadata_score), 6)
        reranked.append(
            Candidate(
                chunk=chunk,
                vector_score=candidate.vector_score,
                keyword_score=keyword_score,
                metadata_score=metadata_score,
                rerank_score=rerank_score,
            )
        )
    return sorted(reranked, key=lambda item: item.rerank_score, reverse=True)


def ranked_chunks(candidates: list[Candidate], score_field: str) -> list[RetrievedChunk]:
    chunks = []
    for rank, candidate in enumerate(candidates, start=1):
        score = candidate.rerank_score if score_field == "rerank" else candidate.vector_score
        chunks.append(
            RetrievedChunk(
                rank=rank,
                score=score,
                strategy=candidate.chunk.strategy,
                chunk_id=candidate.chunk.chunk_id,
                source=candidate.chunk.source,
                title=candidate.chunk.title,
                section=candidate.chunk.section,
                text=candidate.chunk.text,
            )
        )
    return chunks


def deepseek_client() -> tuple[OpenAI, str, int]:
    load_dotenv()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    timeout = parse_float(os.getenv("DAY23_DEEPSEEK_TIMEOUT_SECONDS"), 60.0)
    retries = parse_int(os.getenv("DAY23_DEEPSEEK_RETRIES"), 2)
    return OpenAI(api_key=require_env("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL, timeout=timeout, max_retries=0), model, retries


def ask_deepseek(messages: list[dict[str, str]], mode: str, max_tokens: int = 700) -> LlmResult:
    client, model, retries = deepseek_client()
    started = time.perf_counter()
    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                extra_body=THINKING_DISABLED,
                temperature=0,
                max_tokens=max_tokens,
                messages=messages,
            )
            usage = response.usage
            return LlmResult(
                mode=mode,
                answer=response.choices[0].message.content.strip() if response.choices else "",
                prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
                total_tokens=getattr(usage, "total_tokens", 0) if usage else 0,
                elapsed_seconds=time.perf_counter() - started,
            )
        except APIStatusError as error:
            last_error = str(error)
            if (error.status_code not in {408, 429} and error.status_code < 500) or attempt == retries:
                break
        except (APITimeoutError, APIConnectionError, APIError) as error:
            last_error = str(error)
            if attempt == retries:
                break
        time.sleep(min(2 * attempt, 6))
    return LlmResult(mode, "", 0, 0, 0, time.perf_counter() - started, last_error or "DeepSeek request failed")


def extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def rewrite_query(question: str, fallback_query: str, rewrite_mode: str) -> RewriteResult:
    if rewrite_mode == "none":
        return RewriteResult(fallback_query, [], False, False, "", 0, 0, 0, 0.0)

    messages = [
        {
            "role": "system",
            "content": (
                "Ты переписываешь вопрос пользователя в короткий retrieval query для RAG по Android/Kotlin проекту. "
                "Верни только JSON: {\"query\":\"...\", \"keywords\":[\"...\"]}. "
                "Добавляй вероятные имена классов, функций, endpoints и английские code terms, если они следуют из вопроса."
            ),
        },
        {"role": "user", "content": question},
    ]
    result = ask_deepseek(messages, "query_rewrite", max_tokens=220)
    parsed = extract_json_object(result.answer)
    if parsed and isinstance(parsed.get("query"), str) and parsed["query"].strip():
        keywords = parsed.get("keywords")
        return RewriteResult(
            query=parsed["query"].strip(),
            keywords=[str(item) for item in keywords] if isinstance(keywords, list) else [],
            used_deepseek=True,
            fallback_used=False,
            error="",
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            elapsed_seconds=result.elapsed_seconds,
        )
    return RewriteResult(
        query=fallback_query,
        keywords=[],
        used_deepseek=True,
        fallback_used=True,
        error=result.error or "Could not parse rewrite JSON",
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
        elapsed_seconds=result.elapsed_seconds,
    )


def expanded_query(rewrite: RewriteResult, fallback_query: str, question: str) -> str:
    parts = [rewrite.query]
    fallback = fallback_query.strip()
    if fallback and fallback.lower() != question.strip().lower() and fallback.lower() not in rewrite.query.lower():
        parts.append(fallback)
    combined_terms = normalize_terms(" ".join(parts))
    return " ".join(combined_terms) if combined_terms else " ".join(parts)


def ask_with_chunks(question: str, chunks: list[RetrievedChunk], mode: str, retrieval_note: str) -> LlmResult:
    messages = [
        {
            "role": "system",
            "content": (
                "Ты RAG-агент по проекту AstroTarot. Отвечай только по SOURCES. "
                "Обязательно укажи источники [S1], [S2]. Если источники не дают ответа, скажи это. "
                "Ответь по-русски, кратко, с точными именами endpoints/classes/functions."
            ),
        },
        {
            "role": "user",
            "content": (
                f"MODE:\n{mode}\n\n"
                f"RETRIEVAL:\n{retrieval_note}\n\n"
                f"QUESTION:\n{question}\n\n"
                f"SOURCES:\n{build_rag_context(chunks)}"
            ),
        },
    ]
    return ask_deepseek(messages, mode)


def candidate_payload(candidate: Candidate, final_rank: int) -> dict[str, Any]:
    return {
        **asdict(candidate.chunk),
        "rank": final_rank,
        "vector_score": round(candidate.vector_score, 6),
        "keyword_score": round(candidate.keyword_score, 6),
        "metadata_score": round(candidate.metadata_score, 6),
        "rerank_score": round(candidate.rerank_score, 6),
    }


def question_from_arg(qid_or_text: str) -> ControlQuestion:
    for item in CONTROL_QUESTIONS:
        if item.qid == qid_or_text:
            return item
    return ControlQuestion("custom", qid_or_text, qid_or_text, "", [], [])


def run_mode(
    question: ControlQuestion,
    mode: str,
    index_dir: Path,
    strategy: str,
    initial_k: int,
    final_k: int,
    threshold: float,
    min_keep: int,
    rewrite_mode: str,
    answer: bool = True,
) -> dict[str, Any]:
    if mode == "baseline":
        query = question.search_query or question.question
        initial_limit = final_k
        candidates = retrieve_candidates(index_dir, query, strategy, initial_limit)
        filtered = candidates
        final_candidates = candidates[:final_k]
        fallback_used = False
        rewrite = None
        score_field = "vector"
    elif mode == "filtered":
        query = question.search_query or question.question
        initial_limit = initial_k
        candidates = retrieve_candidates(index_dir, query, strategy, initial_limit)
        filtered, fallback_used = filter_candidates(candidates, threshold, min_keep)
        final_candidates = filtered[:final_k]
        rewrite = None
        score_field = "vector"
    elif mode == "rewrite_rerank":
        fallback_query = question.search_query or question.question
        rewrite = rewrite_query(question.question, fallback_query, rewrite_mode)
        query = expanded_query(rewrite, fallback_query, question.question)
        initial_limit = initial_k
        candidates = retrieve_candidates(index_dir, query, strategy, initial_limit)
        filtered, fallback_used = filter_candidates(candidates, threshold, min_keep)
        final_candidates = rerank_candidates(filtered, f"{question.question} {query}")[:final_k]
        score_field = "rerank"
    else:
        raise ValueError(f"Unknown mode: {mode}")

    chunks = ranked_chunks(final_candidates, score_field)
    retrieval_note = (
        f"query={query}; initial_k={initial_limit}; initial_count={len(candidates)}; "
        f"threshold={threshold if mode != 'baseline' else 'none'}; filtered_count={len(filtered)}; "
        f"final_k={final_k}; final_count={len(chunks)}; fallback_used={fallback_used}"
    )
    llm = ask_with_chunks(question.question, chunks, mode, retrieval_note) if answer else LlmResult(mode, "", 0, 0, 0, 0.0)
    terms = term_hits(llm.answer, question.expected_terms) if question.expected_terms else {"hits": [], "missing": [], "score": 0}
    sources = source_hits(chunks, question.expected_sources) if question.expected_sources else {
        "retrieved_sources": [chunk.source for chunk in chunks],
        "expected_source_hits": [],
        "source_score": 0,
    }

    return {
        "mode": mode,
        "query_used": query,
        "rewrite": asdict(rewrite) if rewrite else None,
        "strategy": strategy,
        "initial_k": initial_limit,
        "initial_count": len(candidates),
        "threshold": threshold if mode != "baseline" else None,
        "filtered_count": len(filtered),
        "final_k": final_k,
        "final_count": len(chunks),
        "fallback_used": fallback_used,
        "retrieved_sources": [chunk.source for chunk in chunks],
        "selected_chunks": [candidate_payload(candidate, index) for index, candidate in enumerate(final_candidates, start=1)],
        "source_eval": sources,
        "llm": {**asdict(llm), "term_eval": terms},
    }


def compare_question(
    question: ControlQuestion,
    modes: list[str],
    index_dir: Path,
    strategy: str,
    initial_k: int,
    final_k: int,
    threshold: float,
    min_keep: int,
    rewrite_mode: str,
) -> dict[str, Any]:
    mode_results = [
        run_mode(question, mode, index_dir, strategy, initial_k, final_k, threshold, min_keep, rewrite_mode)
        for mode in modes
    ]
    baseline = next((item for item in mode_results if item["mode"] == "baseline"), mode_results[0] if mode_results else None)
    baseline_score = baseline["llm"]["term_eval"]["score"] if baseline else 0
    for item in mode_results:
        item["quality_delta_vs_baseline"] = round(item["llm"]["term_eval"]["score"] - baseline_score, 3)
    return {
        "qid": question.qid,
        "question": question.question,
        "expected": question.expected,
        "expected_terms": question.expected_terms,
        "expected_sources": question.expected_sources,
        "modes": mode_results,
    }


def average(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def summarize_results(results: list[dict[str, Any]], modes: list[str]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for mode in modes:
        items = [mode_result for result in results for mode_result in result["modes"] if mode_result["mode"] == mode]
        summary[mode] = {
            "avg_term_score": average([item["llm"]["term_eval"]["score"] for item in items]),
            "avg_source_score": average([item["source_eval"]["source_score"] for item in items]),
            "avg_tokens": average([item["llm"]["total_tokens"] for item in items]),
            "avg_initial_count": average([item["initial_count"] for item in items]),
            "avg_filtered_count": average([item["filtered_count"] for item in items]),
            "avg_final_count": average([item["final_count"] for item in items]),
            "fallbacks": sum(1 for item in items if item["fallback_used"]),
            "avg_delta_vs_baseline": average([item.get("quality_delta_vs_baseline", 0) for item in items]),
        }
    return summary


def build_report(payload: dict[str, Any]) -> str:
    mode_rows = []
    for mode, item in payload["mode_summary"].items():
        mode_rows.append(
            "| {mode} | {term:.3f} | {source:.3f} | {tokens:.1f} | {initial:.1f} | {filtered:.1f} | {final:.1f} | {delta:+.3f} | {fallbacks} |".format(
                mode=mode,
                term=item["avg_term_score"],
                source=item["avg_source_score"],
                tokens=item["avg_tokens"],
                initial=item["avg_initial_count"],
                filtered=item["avg_filtered_count"],
                final=item["avg_final_count"],
                delta=item["avg_delta_vs_baseline"],
                fallbacks=item["fallbacks"],
            )
        )

    question_rows = []
    for result in payload["results"]:
        scores = {item["mode"]: item["llm"]["term_eval"]["score"] for item in result["modes"]}
        sources = {item["mode"]: item["source_eval"]["source_score"] for item in result["modes"]}
        question_rows.append(
            "| {qid} | {baseline:.2f} | {filtered:.2f} | {rewrite:.2f} | {b_src:.2f} | {f_src:.2f} | {r_src:.2f} |".format(
                qid=result["qid"],
                baseline=scores.get("baseline", 0),
                filtered=scores.get("filtered", 0),
                rewrite=scores.get("rewrite_rerank", 0),
                b_src=sources.get("baseline", 0),
                f_src=sources.get("filtered", 0),
                r_src=sources.get("rewrite_rerank", 0),
            )
        )

    return "\n".join(
        [
            "# Day 23. Reranking and Filtering",
            "",
            "## Summary",
            "",
            f"- generated_at: `{payload['generated_at']}`",
            f"- index_dir: `{payload['index_dir']}`",
            f"- strategy: `{payload['strategy']}`",
            f"- initial_k: `{payload['initial_k']}`",
            f"- final_k: `{payload['final_k']}`",
            f"- similarity_threshold: `{payload['similarity_threshold']}`",
            f"- rewrite_mode: `{payload['rewrite_mode']}`",
            "",
            "## Modes",
            "",
            "- `baseline`: top-5 vector search, no filter, no rewrite, no rerank.",
            "- `filtered`: top-20 vector search, similarity threshold, final top-5.",
            "- `rewrite_rerank`: DeepSeek query rewrite, top-20 search, threshold, heuristic rerank, final top-5.",
            "",
            "## Average Comparison",
            "",
            "| mode | avg term score | avg source score | avg tokens | initial | filtered | final | delta vs baseline | fallbacks |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            *mode_rows,
            "",
            "## Per Question",
            "",
            "| qid | baseline terms | filtered terms | rewrite+rerank terms | baseline source | filtered source | rewrite+rerank source |",
            "|---|---:|---:|---:|---:|---:|---:|",
            *question_rows,
            "",
            "## Full Results",
            "",
            "```json",
            to_json(payload["results"]),
            "```",
            "",
            "## Check Commands",
            "",
            "```powershell",
            ".\\.venv\\Scripts\\python.exe day23_rag_rerank_agent.py eval-one q06 --mode all",
            ".\\.venv\\Scripts\\python.exe day23_rag_rerank_agent.py compare --limit 10 --mode all",
            ".\\.venv\\Scripts\\python.exe day23_rag_rerank_agent.py threshold-scan --limit 10",
            "```",
        ]
    )


def save_comparison(payload: dict[str, Any], report_path: str) -> tuple[Path, Path]:
    store = load_store_dir()
    store.mkdir(parents=True, exist_ok=True)
    json_path = store / "last_comparison.json"
    json_path.write_text(to_json(payload), encoding="utf-8")
    report = Path(report_path).resolve()
    report.write_text(build_report(payload), encoding="utf-8")
    return json_path.resolve(), report


def command_ask(args: argparse.Namespace) -> int:
    question = question_from_arg(args.question)
    modes = parse_modes(args.mode)
    index_dir = load_index_dir(args)
    print(f"question={question.question}")
    print(f"modes={','.join(modes)}")
    for mode in modes:
        print(f"\n### {mode}", flush=True)
        result = run_mode(
            question,
            mode,
            index_dir,
            args.strategy,
            args.initial_k,
            args.final_k,
            args.threshold,
            args.min_keep,
            args.rewrite,
        )
        print_mode_result(result, verbose=args.verbose)
    return 0


def print_mode_result(result: dict[str, Any], verbose: bool = False) -> None:
    print(f"query_used={result['query_used']}")
    if result["rewrite"]:
        rewrite = result["rewrite"]
        print(
            f"rewrite_used={rewrite['used_deepseek']} fallback={rewrite['fallback_used']} "
            f"tokens={rewrite['total_tokens']} query={rewrite['query']}"
        )
    print(
        f"initial_k={result['initial_k']} initial_count={result['initial_count']} "
        f"threshold={result['threshold']} filtered_count={result['filtered_count']} "
        f"final_count={result['final_count']} fallback_used={result['fallback_used']}"
    )
    print(
        f"term_score={result['llm']['term_eval']['score']} "
        f"source_score={result['source_eval']['source_score']} tokens={result['llm']['total_tokens']}"
    )
    print("sources:")
    for source in result["retrieved_sources"]:
        print(f"- {source}")
    print("answer:")
    print(result["llm"]["answer"] or result["llm"]["error"])
    if verbose:
        print("selected_chunks:")
        print(to_json(result["selected_chunks"]))


def command_eval_one(args: argparse.Namespace) -> int:
    question = question_from_arg(args.qid)
    if question.qid == "custom":
        raise ValueError(f"Unknown qid: {args.qid}")
    index_dir = load_index_dir(args)
    modes = parse_modes(args.mode)
    result = compare_question(
        question,
        modes,
        index_dir,
        args.strategy,
        args.initial_k,
        args.final_k,
        args.threshold,
        args.min_keep,
        args.rewrite,
    )
    print(f"qid={result['qid']}")
    print(f"question={result['question']}")
    print(f"expected={result['expected']}")
    for item in result["modes"]:
        print(f"\n### {item['mode']}")
        print_mode_result(item, verbose=args.verbose)
    return 0


def command_compare(args: argparse.Namespace) -> int:
    index_dir = load_index_dir(args)
    modes = parse_modes(args.mode)
    selected = CONTROL_QUESTIONS[: args.limit]
    results = []
    for index, question in enumerate(selected, start=1):
        print(f"running={index}/{len(selected)} qid={question.qid}", flush=True)
        result = compare_question(
            question,
            modes,
            index_dir,
            args.strategy,
            args.initial_k,
            args.final_k,
            args.threshold,
            args.min_keep,
            args.rewrite,
        )
        results.append(result)
        compact_scores = ", ".join(
            f"{item['mode']}={item['llm']['term_eval']['score']}/src{item['source_eval']['source_score']}"
            for item in result["modes"]
        )
        print(f"done={question.qid} {compact_scores}", flush=True)

    payload = {
        "generated_at": now_iso(),
        "index_dir": str(index_dir),
        "strategy": args.strategy,
        "initial_k": args.initial_k,
        "final_k": args.final_k,
        "similarity_threshold": args.threshold,
        "min_keep": args.min_keep,
        "rewrite_mode": args.rewrite,
        "mode_summary": summarize_results(results, modes),
        "results": results,
    }
    json_path, report_path = save_comparison(payload, args.report)
    print(f"questions={len(results)}")
    for mode, item in payload["mode_summary"].items():
        print(
            f"{mode}: avg_term={item['avg_term_score']} avg_source={item['avg_source_score']} "
            f"avg_tokens={item['avg_tokens']} delta_vs_baseline={item['avg_delta_vs_baseline']}"
        )
    print(f"json_saved={json_path}")
    print(f"report_saved={report_path}")
    return 0


def command_threshold_scan(args: argparse.Namespace) -> int:
    index_dir = load_index_dir(args)
    thresholds = parse_thresholds(args.thresholds)
    selected = CONTROL_QUESTIONS[: args.limit]
    rows = []
    for threshold in thresholds:
        source_scores = []
        kept_counts = []
        fallback_count = 0
        for question in selected:
            candidates = retrieve_candidates(index_dir, question.search_query, args.strategy, args.initial_k)
            filtered, fallback_used = filter_candidates(candidates, threshold, args.min_keep)
            final_candidates = filtered[: args.final_k]
            chunks = ranked_chunks(final_candidates, "vector")
            sources = source_hits(chunks, question.expected_sources)
            source_scores.append(sources["source_score"])
            kept_counts.append(len(filtered))
            if fallback_used:
                fallback_count += 1
        row = {
            "threshold": threshold,
            "questions": len(selected),
            "avg_source_score": average(source_scores),
            "avg_kept_count": average(kept_counts),
            "fallbacks": fallback_count,
        }
        rows.append(row)
        print(
            f"threshold={threshold:.2f} avg_source={row['avg_source_score']} "
            f"avg_kept={row['avg_kept_count']} fallbacks={fallback_count}"
        )

    payload = {
        "generated_at": now_iso(),
        "retrieval_only": True,
        "index_dir": str(index_dir),
        "strategy": args.strategy,
        "initial_k": args.initial_k,
        "final_k": args.final_k,
        "min_keep": args.min_keep,
        "results": rows,
    }
    store = load_store_dir()
    store.mkdir(parents=True, exist_ok=True)
    path = (store / "threshold_scan.json").resolve()
    path.write_text(to_json(payload), encoding="utf-8")
    print(f"json_saved={path}")
    return 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--index-dir", default=None)
    parser.add_argument("--strategy", choices=["fixed", "structure"], default=os.getenv("DAY23_RAG_STRATEGY", "structure"))
    parser.add_argument("--initial-k", type=int, default=parse_int(os.getenv("DAY23_INITIAL_K"), 20))
    parser.add_argument("--final-k", type=int, default=parse_int(os.getenv("DAY23_FINAL_K"), 5))
    parser.add_argument("--threshold", type=float, default=parse_float(os.getenv("DAY23_SIMILARITY_THRESHOLD"), 0.62))
    parser.add_argument("--min-keep", type=int, default=parse_int(os.getenv("DAY23_MIN_KEEP"), 2))
    parser.add_argument("--rewrite", choices=["deepseek", "none"], default=os.getenv("DAY23_REWRITE_MODE", "deepseek"))


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Day 23 RAG: similarity filtering, query rewrite, and heuristic reranking.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ask_parser = subparsers.add_parser("ask", help="ask one question with selected RAG mode")
    add_common_args(ask_parser)
    ask_parser.add_argument("question", help="Question text or qid")
    ask_parser.add_argument("--mode", choices=[*VALID_MODES, "all"], default="rewrite_rerank")
    ask_parser.add_argument("--verbose", action="store_true")

    eval_parser = subparsers.add_parser("eval-one", help="evaluate one control question")
    add_common_args(eval_parser)
    eval_parser.add_argument("qid")
    eval_parser.add_argument("--mode", choices=[*VALID_MODES, "all"], default="all")
    eval_parser.add_argument("--verbose", action="store_true")

    compare_parser = subparsers.add_parser("compare", help="run control set comparison")
    add_common_args(compare_parser)
    compare_parser.add_argument("--limit", type=int, default=10)
    compare_parser.add_argument("--mode", choices=[*VALID_MODES, "all"], default="all")
    compare_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)

    scan_parser = subparsers.add_parser("threshold-scan", help="scan similarity thresholds without LLM calls")
    add_common_args(scan_parser)
    scan_parser.add_argument("--limit", type=int, default=10)
    scan_parser.add_argument("--thresholds", default="0.55,0.60,0.62,0.65,0.70")

    args = parser.parse_args()
    if args.command == "ask":
        raise SystemExit(command_ask(args))
    if args.command == "eval-one":
        raise SystemExit(command_eval_one(args))
    if args.command == "compare":
        raise SystemExit(command_compare(args))
    if args.command == "threshold-scan":
        raise SystemExit(command_threshold_scan(args))


if __name__ == "__main__":
    main()
