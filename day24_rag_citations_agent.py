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

from day21_document_indexer import db_path
from day22_rag_agent import CONTROL_QUESTIONS as DAY22_CONTROL_QUESTIONS
from day22_rag_agent import ControlQuestion, LlmResult, RetrievedChunk, source_hits, term_hits
from day23_rag_rerank_agent import (
    Candidate,
    average,
    expanded_query,
    extract_json_object,
    filter_candidates,
    normalize_terms,
    overlap_score,
    parse_float,
    parse_int,
    ranked_chunks,
    rerank_candidates,
    retrieve_candidates,
    to_json,
)


DEFAULT_INDEX_DIR = "day21_index_store"
DEFAULT_STORE_DIR = "day24_rag_store"
DEFAULT_REPORT_PATH = "DAY24_CITATIONS_ANTIHALLUCINATION_REPORT.md"
DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
THINKING_DISABLED = {"thinking": {"type": "disabled"}}

DAY24_NEGATIVE_QUESTION = ControlQuestion(
    "q10",
    "Какой backend endpoint оформляет refund для покупки Premium в Stripe?",
    "Stripe refund Premium purchase backend endpoint webhook payment intent",
    "В индексе AstroTarot нет backend endpoint для Stripe refund; агент должен ответить 'не знаю'.",
    [],
    [],
)
DAY24_CONTROL_QUESTIONS = [*DAY22_CONTROL_QUESTIONS[:9], DAY24_NEGATIVE_QUESTION]


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


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def load_index_dir(args: argparse.Namespace) -> Path:
    load_dotenv()
    return Path(args.index_dir or os.getenv("DAY24_RAG_INDEX_DIR", DEFAULT_INDEX_DIR)).resolve()


def load_store_dir() -> Path:
    load_dotenv()
    return Path(os.getenv("DAY24_RAG_STORE_DIR", DEFAULT_STORE_DIR)).resolve()


def deepseek_client() -> tuple[OpenAI, str, int]:
    load_dotenv()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    timeout = parse_float(os.getenv("DAY24_DEEPSEEK_TIMEOUT_SECONDS"), 60.0)
    retries = parse_int(os.getenv("DAY24_DEEPSEEK_RETRIES"), 1)
    return OpenAI(api_key=require_env("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL, timeout=timeout, max_retries=0), model, retries


def ask_deepseek(messages: list[dict[str, str]], mode: str, max_tokens: int = 900) -> LlmResult:
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


def rewrite_query(question: str, fallback_query: str, rewrite_mode: str) -> RewriteResult:
    if rewrite_mode == "none":
        return RewriteResult(fallback_query, [], False, False, "", 0, 0, 0, 0.0)
    result = ask_deepseek(
        [
            {
                "role": "system",
                "content": (
                    "Перепиши вопрос в короткий retrieval query для RAG по Android/Kotlin проекту. "
                    "Верни только JSON: {\"query\":\"...\", \"keywords\":[\"...\"]}."
                ),
            },
            {"role": "user", "content": question},
        ],
        "day24_query_rewrite",
        max_tokens=220,
    )
    parsed = extract_json_object(result.answer)
    if parsed and isinstance(parsed.get("query"), str) and parsed["query"].strip():
        keywords = parsed.get("keywords")
        return RewriteResult(
            parsed["query"].strip(),
            [str(item) for item in keywords] if isinstance(keywords, list) else [],
            True,
            False,
            "",
            result.prompt_tokens,
            result.completion_tokens,
            result.total_tokens,
            result.elapsed_seconds,
        )
    return RewriteResult(
        fallback_query,
        [],
        True,
        True,
        result.error or "Could not parse rewrite JSON",
        result.prompt_tokens,
        result.completion_tokens,
        result.total_tokens,
        result.elapsed_seconds,
    )


def question_from_arg(qid_or_text: str) -> ControlQuestion:
    normalized_qid = qid_or_text.strip().rstrip("\\/")
    for item in DAY24_CONTROL_QUESTIONS:
        if item.qid in {qid_or_text.strip(), normalized_qid}:
            return item
    return ControlQuestion("custom", qid_or_text, qid_or_text, "", [], [])


def compact(text: str, limit: int = 1400) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 20].rstrip() + " ...[truncated]"


def build_sources_context(chunks: list[RetrievedChunk]) -> str:
    parts = []
    for index, chunk in enumerate(chunks, start=1):
        parts.append(
            "\n".join(
                [
                    f"[S{index}]",
                    f"source={chunk.source}",
                    f"section={chunk.section}",
                    f"chunk_id={chunk.chunk_id}",
                    f"relevance={chunk.score:.4f}",
                    "text:",
                    compact(chunk.text),
                ]
            )
        )
    return "\n\n---\n\n".join(parts)


def best_quote(chunk: RetrievedChunk, query: str, limit: int = 320) -> str:
    fragments = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", chunk.text) if part.strip()]
    if not fragments:
        return compact(chunk.text, limit)
    scored = [(overlap_score(query, fragment), len(fragment), fragment) for fragment in fragments]
    scored.sort(key=lambda item: (item[0], -abs(item[1] - 180)), reverse=True)
    return compact(scored[0][2], limit)


def retrieve_lexical_candidates(index_dir: Path, query: str, strategy: str, top_k: int) -> list[Candidate]:
    conn = sqlite3.connect(db_path(index_dir))
    try:
        rows = conn.execute(
            """
            SELECT strategy, chunk_id, source, title, section, text
            FROM chunks
            WHERE strategy = ?
            """,
            (strategy,),
        ).fetchall()
    finally:
        conn.close()

    scored = []
    for row in rows:
        metadata = f"{row[2]} {row[3]} {row[4]}"
        keyword_score = overlap_score(query, f"{metadata} {row[5]}", limit=16)
        metadata_score = overlap_score(query, metadata, limit=10)
        if keyword_score <= 0 and metadata_score <= 0:
            continue
        score = (0.70 * keyword_score) + (0.30 * metadata_score)
        scored.append((score, keyword_score, metadata_score, row))
    scored.sort(key=lambda item: item[0], reverse=True)

    candidates = []
    for rank, (_score, keyword_score, metadata_score, row) in enumerate(scored[:top_k], start=1):
        chunk = RetrievedChunk(
            rank=rank,
            score=0.0,
            strategy=row[0],
            chunk_id=row[1],
            source=row[2],
            title=row[3],
            section=row[4],
            text=row[5],
        )
        rerank_score = round((0.45 * keyword_score) + (0.25 * metadata_score), 6)
        candidates.append(
            Candidate(
                chunk=chunk,
                vector_score=0.0,
                keyword_score=keyword_score,
                metadata_score=metadata_score,
                rerank_score=rerank_score,
            )
        )
    return candidates


def merge_candidates(primary: list[Candidate], supplement: list[Candidate]) -> list[Candidate]:
    merged: dict[str, Candidate] = {candidate.chunk.chunk_id: candidate for candidate in primary}
    for candidate in supplement:
        current = merged.get(candidate.chunk.chunk_id)
        if not current or candidate.rerank_score > current.rerank_score:
            merged[candidate.chunk.chunk_id] = candidate
    return list(merged.values())


def day24_rerank_candidates(candidates: list[Candidate], query_text: str) -> list[Candidate]:
    scored = []
    for candidate in candidates:
        chunk = candidate.chunk
        keyword_score = max(candidate.keyword_score, overlap_score(query_text, chunk.text, limit=16))
        metadata_score = max(candidate.metadata_score, overlap_score(query_text, f"{chunk.source} {chunk.title} {chunk.section}", limit=10))
        rerank_score = round((0.45 * candidate.vector_score) + (0.35 * keyword_score) + (0.20 * metadata_score), 6)
        scored.append(
            Candidate(
                chunk=chunk,
                vector_score=candidate.vector_score,
                keyword_score=keyword_score,
                metadata_score=metadata_score,
                rerank_score=rerank_score,
            )
        )
    return sorted(scored, key=lambda item: item.rerank_score, reverse=True)


def deterministic_sources(chunks: list[RetrievedChunk]) -> list[dict[str, Any]]:
    return [
        {
            "source_id": f"S{index}",
            "source": chunk.source,
            "section": chunk.section,
            "chunk_id": chunk.chunk_id,
        }
        for index, chunk in enumerate(chunks, start=1)
    ]


def deterministic_quotes(chunks: list[RetrievedChunk], query: str, min_quotes: int) -> list[dict[str, str]]:
    return [
        {
            "source_id": f"S{index}",
            "source": chunk.source,
            "section": chunk.section,
            "chunk_id": chunk.chunk_id,
            "quote": best_quote(chunk, query),
        }
        for index, chunk in enumerate(chunks[:min_quotes], start=1)
    ]


def low_relevance_answer(question: str, max_relevance: float, threshold: float) -> dict[str, Any]:
    return {
        "answer": (
            "не знаю. Найденный контекст ниже порога релевантности "
            f"({max_relevance:.3f} < {threshold:.3f}). Уточните вопрос: файл, класс, функцию или endpoint."
        ),
        "sources": [],
        "quotes": [],
        "confidence": "low",
        "needs_clarification": True,
        "question": question,
    }


def ask_with_required_citations(question: str, chunks: list[RetrievedChunk], query: str, _min_quotes: int) -> tuple[dict[str, Any], LlmResult, bool]:
    allowed_quotes = deterministic_quotes(chunks, query, max(_min_quotes, min(5, len(chunks))))
    schema_hint = {
        "answer": "краткий ответ на русском",
        "sources": [{"source_id": "S1", "source": "...", "section": "...", "chunk_id": "..."}],
        "quotes": [{"source_id": "S1", "source": "...", "section": "...", "chunk_id": "...", "quote": "точная цитата из чанка"}],
        "confidence": "high|medium|low",
        "needs_clarification": False,
    }
    result = ask_deepseek(
        [
            {
                "role": "system",
                "content": (
                    "Ты строгий RAG-ассистент по проекту AstroTarot. Используй только SOURCES. "
                    "Верни только JSON без markdown. Обязательные поля: answer, sources, quotes, confidence, needs_clarification. "
                    "sources должны содержать source_id, source, section, chunk_id. "
                    "quotes должны содержать source_id, source, section, chunk_id, quote. "
                    "quote должен быть дословно скопирован из ALLOWED_QUOTES. Не сокращай и не переформатируй quote. "
                    "Дай 1-3 sources и 1-3 quotes. Каждая quote должна быть короткой: 40-240 символов. "
                    "Если контекст не отвечает на вопрос, answer должен начинаться с 'не знаю' и needs_clarification=true."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"QUESTION:\n{question}\n\n"
                    f"RETRIEVAL_QUERY:\n{query}\n\n"
                    f"REQUIRED_JSON_SHAPE:\n{to_json(schema_hint)}\n\n"
                    f"ALLOWED_QUOTES:\n{to_json(allowed_quotes)}\n\n"
                    f"SOURCES:\n{build_sources_context(chunks)}"
                ),
            },
        ],
        "day24_cited_rag",
        max_tokens=1600,
    )
    parsed = extract_json_object(result.answer)
    model_json_valid = True
    if not parsed:
        parsed = {
            "answer": result.answer or result.error or "не знаю. DeepSeek не вернул валидный JSON.",
            "sources": [],
            "quotes": [],
            "confidence": "low",
            "needs_clarification": bool(result.error),
        }
        model_json_valid = False

    parsed.setdefault("answer", "")
    parsed.setdefault("sources", [])
    parsed.setdefault("quotes", [])
    parsed.setdefault("confidence", "medium")
    parsed.setdefault("needs_clarification", False)
    return parsed, result, model_json_valid


def quote_matches_chunk(quote: str, chunk: RetrievedChunk | None) -> bool:
    normalized_quote = re.sub(r"\s+", " ", quote).strip().lower()
    if not normalized_quote or not chunk:
        return False
    normalized_text = re.sub(r"\s+", " ", chunk.text).strip().lower()
    return normalized_quote in normalized_text


def validate_answer(
    parsed: dict[str, Any],
    chunks: list[RetrievedChunk],
    question: ControlQuestion,
    model_json_valid: bool,
    low_relevance_blocked: bool,
) -> dict[str, Any]:
    chunks_by_id = {f"S{index}": chunk for index, chunk in enumerate(chunks, start=1)}
    chunks_by_chunk_id = {chunk.chunk_id: chunk for chunk in chunks}
    sources = parsed.get("sources") if isinstance(parsed.get("sources"), list) else []
    quotes = parsed.get("quotes") if isinstance(parsed.get("quotes"), list) else []
    answer = str(parsed.get("answer", ""))
    source_ids = {source.get("source_id") for source in sources if isinstance(source, dict)}
    source_fields_ok = bool(sources) and all(
        isinstance(source, dict)
        and bool(source.get("source"))
        and bool(source.get("section"))
        and bool(source.get("chunk_id"))
        for source in sources
    )
    quote_fields_ok = bool(quotes) and all(
        isinstance(item, dict)
        and bool(item.get("source"))
        and bool(item.get("section"))
        and bool(item.get("chunk_id"))
        and bool(item.get("quote"))
        for item in quotes
    )
    quote_results = []
    for item in quotes:
        if not isinstance(item, dict):
            quote_results.append(False)
            continue
        quote = str(item.get("quote", ""))
        source_id = str(item.get("source_id", ""))
        chunk_id = str(item.get("chunk_id", ""))
        chunk = chunks_by_id.get(source_id) or chunks_by_chunk_id.get(chunk_id)
        quote_results.append(quote_matches_chunk(quote, chunk))
    answer_eval = term_hits(answer, question.expected_terms) if question.expected_terms else {
        "hits": [],
        "missing": [],
        "score": 0,
    }
    quote_text = " ".join(str(item.get("quote", "")) for item in quotes if isinstance(item, dict))
    quote_eval = term_hits(quote_text, question.expected_terms) if question.expected_terms else {"hits": [], "missing": [], "score": 0}
    answer_quote_overlap = overlap_score(str(parsed.get("answer", "")), quote_text, limit=20)
    meaning_matches = bool(quotes) and all(
        [
            bool(quote_results) and all(quote_results),
            answer_eval["score"] >= 0.25 or quote_eval["score"] >= 0.25 or answer_quote_overlap >= 0.2,
        ]
    )
    low_relevance_rule_ok = (
        low_relevance_blocked
        and answer.lower().startswith("не знаю")
        and bool(parsed.get("needs_clarification"))
        and not sources
        and not quotes
    )
    normal_answer_ok = all(
        [
            not low_relevance_blocked,
            model_json_valid,
            bool(answer.strip()),
            bool(sources),
            source_fields_ok,
            bool(source_ids) and source_ids.issubset(set(chunks_by_id)),
            bool(quotes),
            quote_fields_ok,
            bool(quote_results) and all(quote_results),
            meaning_matches,
        ]
    )
    return {
        "model_json_valid": model_json_valid,
        "has_answer": bool(answer.strip()),
        "has_sources": bool(sources),
        "source_fields_ok": source_fields_ok,
        "has_quotes": bool(quotes),
        "quote_fields_ok": quote_fields_ok,
        "source_ids_valid": bool(source_ids) and source_ids.issubset(set(chunks_by_id)),
        "quotes_match_chunks": bool(quote_results) and all(quote_results),
        "answer_term_score": answer_eval["score"],
        "quote_term_score": quote_eval["score"],
        "answer_quote_overlap": answer_quote_overlap,
        "meaning_matches_quotes": meaning_matches,
        "low_relevance_rule_ok": low_relevance_rule_ok,
        "passed": low_relevance_rule_ok or normal_answer_ok,
    }


def prepare_chunks(
    question: ControlQuestion,
    index_dir: Path,
    strategy: str,
    initial_k: int,
    final_k: int,
    similarity_threshold: float,
    min_keep: int,
    rewrite_mode: str,
) -> dict[str, Any]:
    fallback_query = question.search_query or question.question
    rewrite = rewrite_query(question.question, fallback_query, rewrite_mode)
    query = expanded_query(rewrite, fallback_query, question.question)
    candidates = retrieve_candidates(index_dir, query, strategy, initial_k)
    filtered, fallback_used = filter_candidates(candidates, similarity_threshold, min_keep)
    lexical = retrieve_lexical_candidates(index_dir, query, strategy, top_k=10)
    merged = merge_candidates(filtered, lexical)
    reranked = day24_rerank_candidates(merged, f"{question.question} {query}")
    final_candidates = reranked[:final_k]
    chunks = ranked_chunks(final_candidates, "rerank")
    max_vector = max((candidate.vector_score for candidate in candidates), default=0.0)
    max_keyword = max((candidate.keyword_score for candidate in reranked), default=0.0)
    context_relevance = round((0.70 * max_vector) + (0.30 * max_keyword), 6)
    return {
        "query": query,
        "rewrite": asdict(rewrite),
        "initial_count": len(candidates),
        "filtered_count": len(filtered),
        "lexical_count": len(lexical),
        "merged_count": len(merged),
        "final_count": len(chunks),
        "fallback_used": fallback_used,
        "max_vector_relevance": round(max_vector, 6),
        "max_keyword_relevance": round(max_keyword, 6),
        "context_relevance": context_relevance,
        "chunks": chunks,
        "candidates": final_candidates,
    }


def answer_question(
    question: ControlQuestion,
    index_dir: Path,
    strategy: str,
    initial_k: int,
    final_k: int,
    similarity_threshold: float,
    relevance_threshold: float,
    min_keep: int,
    min_quotes: int,
    rewrite_mode: str,
) -> dict[str, Any]:
    prepared = prepare_chunks(question, index_dir, strategy, initial_k, final_k, similarity_threshold, min_keep, rewrite_mode)
    chunks = prepared["chunks"]
    low_relevance_blocked = prepared["context_relevance"] < relevance_threshold
    if prepared["context_relevance"] < relevance_threshold or not chunks:
        parsed = low_relevance_answer(question.question, prepared["context_relevance"], relevance_threshold)
        llm = LlmResult("day24_low_relevance", "", 0, 0, 0, 0.0)
        model_json_valid = True
    else:
        parsed, llm, model_json_valid = ask_with_required_citations(question.question, chunks, prepared["query"], min_quotes)
    validation = validate_answer(parsed, chunks, question, model_json_valid, low_relevance_blocked)
    expected_source_eval = source_hits(chunks, question.expected_sources) if question.expected_sources else {
        "retrieved_sources": [chunk.source for chunk in chunks],
        "expected_source_hits": [],
        "source_score": 0,
    }
    return {
        "qid": question.qid,
        "question": question.question,
        "expected": question.expected,
        "expected_sources": question.expected_sources,
        "query_used": prepared["query"],
        "rewrite": prepared["rewrite"],
        "initial_count": prepared["initial_count"],
        "filtered_count": prepared["filtered_count"],
        "lexical_count": prepared["lexical_count"],
        "merged_count": prepared["merged_count"],
        "final_count": prepared["final_count"],
        "similarity_threshold": similarity_threshold,
        "relevance_threshold": relevance_threshold,
        "max_vector_relevance": prepared["max_vector_relevance"],
        "max_keyword_relevance": prepared["max_keyword_relevance"],
        "context_relevance": prepared["context_relevance"],
        "fallback_used": prepared["fallback_used"],
        "low_relevance_blocked": low_relevance_blocked,
        "selected_sources": deterministic_sources(chunks),
        "selected_quotes": deterministic_quotes(chunks, prepared["query"], min_quotes),
        "response": parsed,
        "llm": asdict(llm),
        "model_json_valid": model_json_valid,
        "validation": validation,
        "expected_source_eval": expected_source_eval,
    }


def build_report(payload: dict[str, Any]) -> str:
    rows = []
    for item in payload["results"]:
        validation = item["validation"]
        rows.append(
            "| {qid} | {passed} | {sources} | {quotes} | {quote_match} | {meaning} | {unknown} | {rel:.3f} | {tokens} |".format(
                qid=item["qid"],
                passed="yes" if validation["passed"] else "no",
                sources="yes" if validation["has_sources"] else "no",
                quotes="yes" if validation["has_quotes"] else "no",
                quote_match="yes" if validation["quotes_match_chunks"] else "no",
                meaning="yes" if validation["meaning_matches_quotes"] else "no",
                unknown="yes" if item["low_relevance_blocked"] else "no",
                rel=item["context_relevance"],
                tokens=item["llm"]["total_tokens"],
            )
        )
    details = []
    for item in payload["results"]:
        details.extend(
            [
                f"### {item['qid']}",
                "",
                f"**Question:** {item['question']}",
                "",
                "**Ответ:**",
                "",
                str(item["response"].get("answer", "")),
                "",
                "**Источники:**",
                "",
                *format_sources_for_report(item["response"].get("sources", [])),
                "",
                "**Цитаты:**",
                "",
                *format_quotes_for_report(item["response"].get("quotes", [])),
                "",
                "**Validation:**",
                "",
                "```json",
                to_json(item["validation"]),
                "```",
                "",
            ]
        )
    return "\n".join(
        [
            "# Day 24. Citations, Sources, Anti-Hallucination",
            "",
            "## Summary",
            "",
            f"- generated_at: `{payload['generated_at']}`",
            f"- index_dir: `{payload['index_dir']}`",
            f"- strategy: `{payload['strategy']}`",
            f"- initial_k: `{payload['initial_k']}`",
            f"- final_k: `{payload['final_k']}`",
            f"- similarity_threshold: `{payload['similarity_threshold']}`",
            f"- relevance_threshold: `{payload['relevance_threshold']}`",
            f"- questions: `{len(payload['results'])}`",
            f"- passed: `{payload['summary']['passed']}`",
            f"- answers_with_sources: `{payload['summary']['answers_with_sources']}`",
            f"- answers_with_quotes: `{payload['summary']['answers_with_quotes']}`",
            f"- quotes_match_chunks: `{payload['summary']['quotes_match_chunks']}`",
            f"- meaning_matches_quotes: `{payload['summary']['meaning_matches_quotes']}`",
            f"- low_relevance_blocks: `{payload['summary']['low_relevance_blocks']}`",
            "",
            "## Checks",
            "",
            "| qid | passed | sources | quotes | quotes from chunks | meaning matches quotes | unknown mode | context relevance | tokens |",
            "|---|---|---|---|---|---|---|---:|---:|",
            *rows,
            "",
            "## Answers, Sources, Quotes",
            "",
            *details,
            "## Full Results",
            "",
            "```json",
            to_json(payload["results"]),
            "```",
            "",
            "## Check Commands",
            "",
            "```powershell",
            ".\\.venv\\Scripts\\python.exe day24_rag_citations_agent.py eval-one q06",
            ".\\.venv\\Scripts\\python.exe day24_rag_citations_agent.py compare --limit 10",
            "```",
        ]
    )


def format_sources_for_report(sources: Any) -> list[str]:
    if not isinstance(sources, list) or not sources:
        return ["- нет"]
    rows = []
    for source in sources:
        if isinstance(source, dict):
            rows.append(f"- `{source.get('source')}` | `{source.get('section')}` | `{source.get('chunk_id')}`")
    return rows or ["- нет"]


def format_quotes_for_report(quotes: Any) -> list[str]:
    if not isinstance(quotes, list) or not quotes:
        return ["- нет"]
    rows = []
    for quote in quotes:
        if isinstance(quote, dict):
            rows.append(
                f"- `{quote.get('source')}` | `{quote.get('section')}` | `{quote.get('chunk_id')}`: "
                f"{quote.get('quote')}"
            )
    return rows or ["- нет"]


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "passed": sum(1 for item in results if item["validation"]["passed"]),
        "answers_with_sources": sum(1 for item in results if item["validation"]["has_sources"]),
        "answers_with_quotes": sum(1 for item in results if item["validation"]["has_quotes"]),
        "quotes_match_chunks": sum(1 for item in results if item["validation"]["quotes_match_chunks"]),
        "meaning_matches_quotes": sum(1 for item in results if item["validation"]["meaning_matches_quotes"]),
        "low_relevance_blocks": sum(1 for item in results if item["low_relevance_blocked"]),
        "avg_relevance": average([item["context_relevance"] for item in results]),
        "avg_tokens": average([item["llm"]["total_tokens"] for item in results]),
    }


def save_report(payload: dict[str, Any], report_path: str) -> tuple[Path, Path]:
    store = load_store_dir()
    store.mkdir(parents=True, exist_ok=True)
    json_path = (store / "last_eval.json").resolve()
    json_path.write_text(to_json(payload), encoding="utf-8")
    report = Path(report_path).resolve()
    report.write_text(build_report(payload), encoding="utf-8")
    return json_path, report


def print_result(result: dict[str, Any], verbose: bool = False) -> None:
    print(f"qid={result['qid']}")
    print(f"question={result['question']}")
    print(f"query_used={result['query_used']}")
    print(
        f"context_relevance={result['context_relevance']} vector={result['max_vector_relevance']} "
        f"keyword={result['max_keyword_relevance']} threshold={result['relevance_threshold']} "
        f"low_relevance_blocked={result['low_relevance_blocked']}"
    )
    print(
        f"sources={result['validation']['has_sources']} quotes={result['validation']['has_quotes']} "
        f"quotes_match_chunks={result['validation']['quotes_match_chunks']} "
        f"meaning_matches_quotes={result['validation']['meaning_matches_quotes']} "
        f"passed={result['validation']['passed']}"
    )
    print(f"tokens={result['llm']['total_tokens']} model_json_valid={result['model_json_valid']}")
    print("Ответ:")
    print(result["response"].get("answer", ""))
    print("Источники:")
    for source in result["response"].get("sources", []):
        if isinstance(source, dict):
            print(f"- {source.get('source')} | {source.get('section')} | {source.get('chunk_id')}")
    print("Цитаты:")
    for quote in result["response"].get("quotes", []):
        if isinstance(quote, dict):
            print(f"- {quote.get('source')} | {quote.get('section')} | {quote.get('chunk_id')}: {quote.get('quote')}")
    if verbose:
        print("full_json:")
        print(to_json(result))


def common_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "index_dir": load_index_dir(args),
        "strategy": args.strategy,
        "initial_k": args.initial_k,
        "final_k": args.final_k,
        "similarity_threshold": args.similarity_threshold,
        "relevance_threshold": args.relevance_threshold,
        "min_keep": args.min_keep,
        "min_quotes": args.min_quotes,
        "rewrite_mode": args.rewrite,
    }


def command_ask(args: argparse.Namespace) -> int:
    result = answer_question(question_from_arg(args.question), **common_kwargs(args))
    print_result(result, args.verbose)
    return 0


def command_eval_one(args: argparse.Namespace) -> int:
    question = question_from_arg(args.qid)
    if question.qid == "custom":
        known = ", ".join(item.qid for item in DAY24_CONTROL_QUESTIONS)
        raise ValueError(f"Unknown qid: {args.qid}. Known qids: {known}")
    result = answer_question(question, **common_kwargs(args))
    print_result(result, args.verbose)
    return 0


def handle_cli_error(error: Exception) -> int:
    message = str(error)
    if "Ollama is not available" in message:
        print("ERROR: Ollama is not running or not reachable at http://localhost:11434", file=sys.stderr)
        print("Fix:", file=sys.stderr)
        print("  1. Start Ollama:", file=sys.stderr)
        print("     ollama serve", file=sys.stderr)
        print("  2. Install embedding model once:", file=sys.stderr)
        print("     ollama pull nomic-embed-text", file=sys.stderr)
        print("  3. Check connection:", file=sys.stderr)
        print("     .\\.venv\\Scripts\\python.exe day21_document_indexer.py check-ollama", file=sys.stderr)
        print("  4. Run again:", file=sys.stderr)
        print("     .\\.venv\\Scripts\\python.exe day24_rag_citations_agent.py eval-one q06", file=sys.stderr)
        return 2
    print(f"ERROR: {message}", file=sys.stderr)
    return 2


def command_compare(args: argparse.Namespace) -> int:
    kwargs = common_kwargs(args)
    selected = DAY24_CONTROL_QUESTIONS[: args.limit]
    results = []
    for index, question in enumerate(selected, start=1):
        print(f"running={index}/{len(selected)} qid={question.qid}", flush=True)
        result = answer_question(question, **kwargs)
        results.append(result)
        print(
            f"done={question.qid} sources={result['validation']['has_sources']} "
            f"quotes={result['validation']['has_quotes']} quote_match={result['validation']['quotes_match_chunks']} "
            f"meaning={result['validation']['meaning_matches_quotes']} unknown={result['low_relevance_blocked']} "
            f"passed={result['validation']['passed']}",
            flush=True,
        )

    payload = {
        "generated_at": now_iso(),
        "index_dir": str(kwargs["index_dir"]),
        "strategy": args.strategy,
        "initial_k": args.initial_k,
        "final_k": args.final_k,
        "similarity_threshold": args.similarity_threshold,
        "relevance_threshold": args.relevance_threshold,
        "min_quotes": args.min_quotes,
        "rewrite_mode": args.rewrite,
        "summary": summarize(results),
        "results": results,
    }
    json_path, report_path = save_report(payload, args.report)
    print(f"questions={len(results)}")
    print(f"answers_with_sources={payload['summary']['answers_with_sources']}")
    print(f"answers_with_quotes={payload['summary']['answers_with_quotes']}")
    print(f"quotes_match_chunks={payload['summary']['quotes_match_chunks']}")
    print(f"meaning_matches_quotes={payload['summary']['meaning_matches_quotes']}")
    print(f"low_relevance_blocks={payload['summary']['low_relevance_blocks']}")
    print(f"passed={payload['summary']['passed']}")
    print(f"json_saved={json_path}")
    print(f"report_saved={report_path}")
    return 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--index-dir", default=None)
    parser.add_argument("--strategy", choices=["fixed", "structure"], default=os.getenv("DAY24_RAG_STRATEGY", "structure"))
    parser.add_argument("--initial-k", type=int, default=parse_int(os.getenv("DAY24_INITIAL_K"), 20))
    parser.add_argument("--final-k", type=int, default=parse_int(os.getenv("DAY24_FINAL_K"), 5))
    parser.add_argument("--similarity-threshold", type=float, default=parse_float(os.getenv("DAY24_SIMILARITY_THRESHOLD"), 0.62))
    parser.add_argument("--relevance-threshold", type=float, default=parse_float(os.getenv("DAY24_RELEVANCE_THRESHOLD"), 0.62))
    parser.add_argument("--min-keep", type=int, default=parse_int(os.getenv("DAY24_MIN_KEEP"), 2))
    parser.add_argument("--min-quotes", type=int, default=parse_int(os.getenv("DAY24_MIN_QUOTES"), 2))
    parser.add_argument("--rewrite", choices=["none", "deepseek"], default=os.getenv("DAY24_REWRITE_MODE", "none"))


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Day 24 RAG: required sources, quotes, and low relevance 'не знаю' mode.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ask_parser = subparsers.add_parser("ask", help="ask one custom question or qid")
    add_common_args(ask_parser)
    ask_parser.add_argument("question")
    ask_parser.add_argument("--verbose", action="store_true")

    eval_parser = subparsers.add_parser("eval-one", help="run one control question by qid")
    add_common_args(eval_parser)
    eval_parser.add_argument("qid")
    eval_parser.add_argument("--verbose", action="store_true")

    compare_parser = subparsers.add_parser("compare", help="check required sources and quotes on control questions")
    add_common_args(compare_parser)
    compare_parser.add_argument("--limit", type=int, default=10)
    compare_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)

    args = parser.parse_args()
    try:
        if args.command == "ask":
            raise SystemExit(command_ask(args))
        if args.command == "eval-one":
            raise SystemExit(command_eval_one(args))
        if args.command == "compare":
            raise SystemExit(command_compare(args))
    except (RuntimeError, ValueError) as error:
        raise SystemExit(handle_cli_error(error)) from None


if __name__ == "__main__":
    main()
