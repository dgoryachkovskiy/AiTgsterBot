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

from day21_document_indexer import (
    blob_to_vector,
    cosine_from_normalized,
    create_embedder,
    db_path,
    load_summary,
)


DEFAULT_INDEX_DIR = "day21_index_store"
DEFAULT_STORE_DIR = "day22_rag_store"
DEFAULT_REPORT_PATH = "DAY22_FIRST_RAG_QUERY_REPORT.md"
DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
THINKING_DISABLED = {"thinking": {"type": "disabled"}}


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class ControlQuestion:
    qid: str
    question: str
    search_query: str
    expected: str
    expected_terms: list[str]
    expected_sources: list[str]


@dataclass(frozen=True)
class RetrievedChunk:
    rank: int
    score: float
    strategy: str
    chunk_id: str
    source: str
    title: str
    section: str
    text: str


@dataclass(frozen=True)
class LlmResult:
    mode: str
    answer: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    elapsed_seconds: float
    error: str = ""


CONTROL_QUESTIONS = [
    ControlQuestion(
        "q01",
        "Какой endpoint Android клиент вызывает для проверки email-кода и какие поля отправляет?",
        "verifyEmailCode /v1/auth/verify-email-code email code BackendApiClient",
        "Должен быть POST /v1/auth/verify-email-code с полями email и code.",
        ["/v1/auth/verify-email-code", "email", "code", "verifyEmailCode"],
        ["BackendApiClient.kt", "SignUpViewModel.kt", "Models.kt"],
    ),
    ControlQuestion(
        "q02",
        "Какой endpoint используется для повторной отправки email verification code?",
        "resendEmailVerification /v1/auth/resend-verification email BackendApiClient",
        "Должен быть POST /v1/auth/resend-verification с email.",
        ["/v1/auth/resend-verification", "email", "resendEmailVerification"],
        ["BackendApiClient.kt", "SignUpViewModel.kt", "Repositories.kt"],
    ),
    ControlQuestion(
        "q03",
        "Какая минимальная длина пароля проверяется при регистрации?",
        "weak_password password length 8 SignUpViewModel Repositories",
        "Пароль должен быть минимум 8 символов; backend возвращает weak_password.",
        ["8", "weak_password", "password", "SignUpViewModel"],
        ["SignUpViewModel.kt", "Repositories.kt"],
    ),
    ControlQuestion(
        "q04",
        "Какие основные Screen routes объявлены в навигации AstroTarot?",
        "sealed class Screen route start welcome login signup password_recovery home horoscope tarot moon profile",
        "Routes: start, welcome, login, signup, password_recovery, home, horoscope, tarot, moon, profile.",
        ["start", "welcome", "login", "signup", "password_recovery", "home", "horoscope", "tarot", "moon", "profile"],
        ["Navigation.kt"],
    ),
    ControlQuestion(
        "q05",
        "Когда показывается BottomNavigationBar?",
        "BottomNavigationBar currentRoute bottomRoutes home horoscope tarot profile Navigation",
        "BottomNavigationBar показывается только на bottomRoutes: home, horoscope, tarot, profile.",
        ["BottomNavigationBar", "bottomRoutes", "home", "horoscope", "tarot", "profile"],
        ["Navigation.kt"],
    ),
    ControlQuestion(
        "q06",
        "Как BackendApiClient обновляет access token после 401?",
        "BackendApiClient 401 refreshSession /v1/auth/refresh refreshToken sessionRepository",
        "При 401 authenticated request вызывает refreshSession, POST /v1/auth/refresh, сохраняет новую session или очищает ее.",
        ["401", "refreshSession", "/v1/auth/refresh", "refreshToken", "sessionRepository"],
        ["BackendApiClient.kt"],
    ),
    ControlQuestion(
        "q07",
        "Какие endpoints перечислены в backend README и какие требуют Authorization?",
        "backend README Main endpoints Authorization Bearer /v1 auth require",
        "README перечисляет /health и /v1 endpoints; все /v1/* кроме auth требуют Authorization: Bearer.",
        ["/health", "/v1/auth/signup", "/v1/profile", "Authorization", "Bearer"],
        ["backend\\README.md"],
    ),
    ControlQuestion(
        "q08",
        "Какой backend stack и storage описаны в README?",
        "AstroTarot Backend Ktor PostgreSQL Firebase Firestore own database README",
        "Backend: Ktor + PostgreSQL, хранит user data в собственной базе вместо Firebase/Firestore.",
        ["Ktor", "PostgreSQL", "database", "Firebase", "Firestore"],
        ["backend\\README.md"],
    ),
    ControlQuestion(
        "q09",
        "Где backend хранит историю tarot spread и какие операции есть?",
        "tarot_spread_history listSpreads saveSpread renameSpread deleteSpread Repositories",
        "История хранится в tarot_spread_history; есть list/save/rename/delete spread history.",
        ["tarot_spread_history", "listSpreads", "saveSpread", "renameSpread", "deleteSpread"],
        ["Repositories.kt", "TarotSpreadHistoryEntity.kt"],
    ),
    ControlQuestion(
        "q10",
        "Какой prompt использует AiService для daily tarot insight?",
        "AiService tarotDaily Write a daily focus for tarot card 55-85 words no markdown future guarantees",
        "AiService просит Write a daily focus for tarot card, 55-85 words, one paragraph, no markdown or future guarantees.",
        ["tarotDaily", "Write a daily focus", "55-85 words", "no markdown", "future guarantees"],
        ["AiService.kt"],
    ),
]


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
    return Path(args.index_dir or os.getenv("DAY22_RAG_INDEX_DIR", DEFAULT_INDEX_DIR)).resolve()


def load_store_dir() -> Path:
    load_dotenv()
    return Path(os.getenv("DAY22_RAG_STORE_DIR", DEFAULT_STORE_DIR)).resolve()


def model_to_provider(provider: str) -> str:
    return "hash" if provider == "local_hashing_v1" else provider


def compact(text: str, limit: int = 1600) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 20].rstrip() + " ...[truncated]"


def retrieve_chunks(index_dir: Path, query: str, strategy: str, top_k: int) -> list[RetrievedChunk]:
    summary = load_summary(index_dir)
    embedder = create_embedder(model_to_provider(summary["embedding_provider"]), summary["embedding_dim"])
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
    chunks = []
    for rank, (score, row) in enumerate(scored[:top_k], start=1):
        chunks.append(
            RetrievedChunk(
                rank=rank,
                score=score,
                strategy=row[0],
                chunk_id=row[1],
                source=row[2],
                title=row[3],
                section=row[4],
                text=row[5],
            )
        )
    return chunks


def build_rag_context(chunks: list[RetrievedChunk]) -> str:
    parts = []
    for index, chunk in enumerate(chunks, start=1):
        parts.append(
            "\n".join(
                [
                    f"[S{index}] source={chunk.source}",
                    f"title={chunk.title}",
                    f"section={chunk.section}",
                    f"chunk_id={chunk.chunk_id}",
                    f"score={chunk.score:.4f}",
                    "text:",
                    compact(chunk.text),
                ]
            )
        )
    return "\n\n---\n\n".join(parts)


def deepseek_client() -> tuple[OpenAI, str, int]:
    load_dotenv()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    timeout = parse_float(os.getenv("DAY22_DEEPSEEK_TIMEOUT_SECONDS"), 60.0)
    retries = parse_int(os.getenv("DAY22_DEEPSEEK_RETRIES"), 2)
    return OpenAI(api_key=require_env("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL, timeout=timeout, max_retries=0), model, retries


def ask_deepseek(messages: list[dict[str, str]], mode: str) -> LlmResult:
    client, model, retries = deepseek_client()
    started = time.perf_counter()
    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                extra_body=THINKING_DISABLED,
                temperature=0,
                max_tokens=700,
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


def ask_without_rag(question: str) -> LlmResult:
    messages = [
        {
            "role": "system",
            "content": (
                "Ты отвечаешь без доступа к базе AstroTarot и без RAG. "
                "Если не знаешь точный код проекта, честно скажи, что без контекста нельзя подтвердить детали. "
                "Ответь по-русски, кратко."
            ),
        },
        {"role": "user", "content": question},
    ]
    return ask_deepseek(messages, "no_rag")


def ask_with_rag(question: str, chunks: list[RetrievedChunk]) -> LlmResult:
    messages = [
        {
            "role": "system",
            "content": (
                "Ты RAG-агент по проекту AstroTarot. Отвечай только по SOURCES. "
                "Укажи источники в формате [S1], [S2]. Если источники не дают ответа, скажи это. "
                "Ответь по-русски, кратко, с точными именами endpoints/classes/functions."
            ),
        },
        {
            "role": "user",
            "content": f"QUESTION:\n{question}\n\nSOURCES:\n{build_rag_context(chunks)}",
        },
    ]
    return ask_deepseek(messages, "rag")


def term_hits(answer: str, terms: list[str]) -> dict[str, Any]:
    lower = answer.lower()
    hits = [term for term in terms if term.lower() in lower]
    return {
        "hits": hits,
        "missing": [term for term in terms if term not in hits],
        "score": round(len(hits) / len(terms), 3) if terms else 0,
    }


def source_hits(chunks: list[RetrievedChunk], expected_sources: list[str]) -> dict[str, Any]:
    sources = [chunk.source for chunk in chunks]
    hits = []
    for expected in expected_sources:
        normalized = expected.lower().replace("/", "\\")
        if any(normalized in source.lower().replace("/", "\\") for source in sources):
            hits.append(expected)
    return {
        "retrieved_sources": sources,
        "expected_source_hits": hits,
        "source_score": round(len(hits) / len(expected_sources), 3) if expected_sources else 0,
    }


def find_question(qid_or_text: str) -> ControlQuestion | None:
    for item in CONTROL_QUESTIONS:
        if item.qid == qid_or_text:
            return item
    return None


def compare_one(question: ControlQuestion, index_dir: Path, strategy: str, top_k: int) -> dict[str, Any]:
    chunks = retrieve_chunks(index_dir, question.search_query, strategy, top_k)
    no_rag = ask_without_rag(question.question)
    rag = ask_with_rag(question.question, chunks)
    no_rag_terms = term_hits(no_rag.answer, question.expected_terms)
    rag_terms = term_hits(rag.answer, question.expected_terms)
    sources = source_hits(chunks, question.expected_sources)
    return {
        "qid": question.qid,
        "question": question.question,
        "search_query": question.search_query,
        "expected": question.expected,
        "expected_terms": question.expected_terms,
        "expected_sources": question.expected_sources,
        "strategy": strategy,
        "top_k": top_k,
        "retrieved_chunks": [asdict(chunk) for chunk in chunks],
        "source_eval": sources,
        "no_rag": {**asdict(no_rag), "term_eval": no_rag_terms},
        "rag": {**asdict(rag), "term_eval": rag_terms},
        "quality_delta": round(rag_terms["score"] - no_rag_terms["score"], 3),
    }


def build_report(payload: dict[str, Any]) -> str:
    rows = []
    for item in payload["results"]:
        rows.append(
            "| {qid} | {no_rag:.2f} | {rag:.2f} | {delta:+.2f} | {source:.2f} | {top} |".format(
                qid=item["qid"],
                no_rag=item["no_rag"]["term_eval"]["score"],
                rag=item["rag"]["term_eval"]["score"],
                delta=item["quality_delta"],
                source=item["source_eval"]["source_score"],
                top=(item["source_eval"]["retrieved_sources"][0] if item["source_eval"]["retrieved_sources"] else ""),
            )
        )
    return "\n".join(
        [
            "# Day 22. First RAG Query",
            "",
            "## Summary",
            "",
            f"- generated_at: `{payload['generated_at']}`",
            f"- index_dir: `{payload['index_dir']}`",
            f"- strategy: `{payload['strategy']}`",
            f"- top_k: `{payload['top_k']}`",
            f"- questions: `{len(payload['results'])}`",
            f"- avg_no_rag_score: `{payload['avg_no_rag_score']}`",
            f"- avg_rag_score: `{payload['avg_rag_score']}`",
            f"- avg_quality_delta: `{payload['avg_quality_delta']}`",
            "",
            "## Quality Table",
            "",
            "| qid | no RAG terms | RAG terms | delta | source hit | top source |",
            "|---|---:|---:|---:|---:|---|",
            *rows,
            "",
            "## Control Questions",
            "",
            "```json",
            to_json(
                [
                    {
                        "qid": q.qid,
                        "question": q.question,
                        "search_query": q.search_query,
                        "expected": q.expected,
                        "expected_sources": q.expected_sources,
                    }
                    for q in CONTROL_QUESTIONS
                ]
            ),
            "```",
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
            ".\\.venv\\Scripts\\python.exe day22_rag_agent.py questions",
            ".\\.venv\\Scripts\\python.exe day22_rag_agent.py ask \"Какой endpoint Android клиент вызывает для проверки email-кода?\" --mode both",
            ".\\.venv\\Scripts\\python.exe day22_rag_agent.py compare",
            "```",
        ]
    )


def save_payload(payload: dict[str, Any], report_path: str) -> tuple[Path, Path]:
    store = load_store_dir()
    store.mkdir(parents=True, exist_ok=True)
    json_path = store / "last_comparison.json"
    json_path.write_text(to_json(payload), encoding="utf-8")
    report = Path(report_path).resolve()
    report.write_text(build_report(payload), encoding="utf-8")
    return json_path.resolve(), report


def summarize_payload(results: list[dict[str, Any]], index_dir: Path, strategy: str, top_k: int) -> dict[str, Any]:
    no_scores = [item["no_rag"]["term_eval"]["score"] for item in results]
    rag_scores = [item["rag"]["term_eval"]["score"] for item in results]
    deltas = [item["quality_delta"] for item in results]
    return {
        "generated_at": now_iso(),
        "index_dir": str(index_dir),
        "strategy": strategy,
        "top_k": top_k,
        "avg_no_rag_score": round(sum(no_scores) / len(no_scores), 3) if no_scores else 0,
        "avg_rag_score": round(sum(rag_scores) / len(rag_scores), 3) if rag_scores else 0,
        "avg_quality_delta": round(sum(deltas) / len(deltas), 3) if deltas else 0,
        "results": results,
    }


def print_result(result: dict[str, Any], verbose: bool) -> None:
    print(f"qid={result['qid']}")
    print(f"question={result['question']}")
    print(f"expected={result['expected']}")
    print(f"source_score={result['source_eval']['source_score']}")
    print(f"no_rag_score={result['no_rag']['term_eval']['score']} tokens={result['no_rag']['total_tokens']}")
    print(f"rag_score={result['rag']['term_eval']['score']} tokens={result['rag']['total_tokens']}")
    print(f"quality_delta={result['quality_delta']}")
    print("retrieved_sources:")
    for source in result["source_eval"]["retrieved_sources"]:
        print(f"- {source}")
    print("no_rag_answer:")
    print(result["no_rag"]["answer"] or result["no_rag"]["error"])
    print("rag_answer:")
    print(result["rag"]["answer"] or result["rag"]["error"])
    if verbose:
        print("retrieved_chunks:")
        print(to_json(result["retrieved_chunks"]))


def command_questions(_args: argparse.Namespace) -> int:
    print(f"questions_count={len(CONTROL_QUESTIONS)}")
    for question in CONTROL_QUESTIONS:
        print(f"{question.qid}: {question.question}")
        print(f"  expected={question.expected}")
        print(f"  expected_sources={question.expected_sources}")
    return 0


def command_ask(args: argparse.Namespace) -> int:
    index_dir = load_index_dir(args)
    control = find_question(args.question)
    question = control or ControlQuestion("custom", args.question, args.question, "", [], [])
    chunks = retrieve_chunks(index_dir, question.search_query, args.strategy, args.top_k)
    print(f"question={question.question}")
    print(f"mode={args.mode}")
    if args.mode in {"rag", "both"}:
        print("retrieved_sources:")
        for chunk in chunks:
            print(f"- rank={chunk.rank} score={chunk.score:.4f} source={chunk.source} section={chunk.section}")
    if args.mode in {"no-rag", "both"}:
        no_rag = ask_without_rag(question.question)
        print("no_rag_answer:")
        print(no_rag.answer or no_rag.error)
        print(f"no_rag_tokens={no_rag.total_tokens}")
    if args.mode in {"rag", "both"}:
        rag = ask_with_rag(question.question, chunks)
        print("rag_answer:")
        print(rag.answer or rag.error)
        print(f"rag_tokens={rag.total_tokens}")
    return 0


def command_compare(args: argparse.Namespace) -> int:
    index_dir = load_index_dir(args)
    selected = CONTROL_QUESTIONS[: args.limit]
    results = []
    for index, question in enumerate(selected, start=1):
        print(f"running={index}/{len(selected)} qid={question.qid}", flush=True)
        result = compare_one(question, index_dir, args.strategy, args.top_k)
        results.append(result)
        print(
            f"done={question.qid} no_rag={result['no_rag']['term_eval']['score']} "
            f"rag={result['rag']['term_eval']['score']} source={result['source_eval']['source_score']}",
            flush=True,
        )
    payload = summarize_payload(results, index_dir, args.strategy, args.top_k)
    json_path, report_path = save_payload(payload, args.report)
    print(f"questions={len(results)}")
    print(f"avg_no_rag_score={payload['avg_no_rag_score']}")
    print(f"avg_rag_score={payload['avg_rag_score']}")
    print(f"avg_quality_delta={payload['avg_quality_delta']}")
    print(f"json_saved={json_path}")
    print(f"report_saved={report_path}")
    return 0


def command_eval_one(args: argparse.Namespace) -> int:
    question = find_question(args.qid)
    if not question:
        raise ValueError(f"Unknown qid: {args.qid}")
    result = compare_one(question, load_index_dir(args), args.strategy, args.top_k)
    print_result(result, args.verbose)
    return 0


def add_rag_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--index-dir", default=None)
    parser.add_argument("--strategy", choices=["fixed", "structure"], default=os.getenv("DAY22_RAG_STRATEGY", "structure"))
    parser.add_argument("--top-k", type=int, default=parse_int(os.getenv("DAY22_RAG_TOP_K"), 5))


def main() -> None:
    parser = argparse.ArgumentParser(description="Day 22 first RAG query: no-RAG vs RAG with real DeepSeek API.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("questions", help="print 10 control questions")

    ask_parser = subparsers.add_parser("ask", help="ask one question in no-rag/rag/both mode")
    add_rag_args(ask_parser)
    ask_parser.add_argument("question", help="Question text or qid")
    ask_parser.add_argument("--mode", choices=["no-rag", "rag", "both"], default="both")

    compare_parser = subparsers.add_parser("compare", help="run 10-question quality comparison")
    add_rag_args(compare_parser)
    compare_parser.add_argument("--limit", type=int, default=10)
    compare_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)

    eval_parser = subparsers.add_parser("eval-one", help="compare one control question by qid")
    add_rag_args(eval_parser)
    eval_parser.add_argument("qid")
    eval_parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    if args.command == "questions":
        raise SystemExit(command_questions(args))
    if args.command == "ask":
        raise SystemExit(command_ask(args))
    if args.command == "compare":
        raise SystemExit(command_compare(args))
    if args.command == "eval-one":
        raise SystemExit(command_eval_one(args))


if __name__ == "__main__":
    main()
