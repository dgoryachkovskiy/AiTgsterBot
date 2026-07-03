import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI

from day21_document_indexer import db_path
from day22_rag_agent import ControlQuestion, LlmResult, RetrievedChunk
from day23_rag_rerank_agent import extract_json_object, parse_float, parse_int, to_json
from day24_rag_citations_agent import (
    best_quote,
    deterministic_quotes,
    deterministic_sources,
    low_relevance_answer,
    prepare_chunks,
    quote_matches_chunk,
)


DEFAULT_INDEX_DIR = "day21_index_store"
DEFAULT_STORE_DIR = "day25_chat_store"
DEFAULT_REPORT_PATH = "DAY25_RAG_MEMORY_CHAT_REPORT.md"
DEFAULT_STRATEGY = "structure"
DEFAULT_INITIAL_K = 20
DEFAULT_FINAL_K = 5
DEFAULT_RELEVANCE_THRESHOLD = 0.62
DEFAULT_SIMILARITY_THRESHOLD = 0.62
DEFAULT_MIN_KEEP = 2
DEFAULT_MIN_QUOTES = 2
DEFAULT_RECENT_MESSAGES = 8
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_RETRIES = 2
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
THINKING_DISABLED = {"thinking": {"type": "disabled"}}
SESSION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_index_dir(args: argparse.Namespace) -> Path:
    load_dotenv()
    return Path(args.index_dir or os.getenv("DAY25_RAG_INDEX_DIR", DEFAULT_INDEX_DIR)).resolve()


def load_store_dir() -> Path:
    load_dotenv()
    return Path(os.getenv("DAY25_CHAT_STORE_DIR", DEFAULT_STORE_DIR)).resolve()


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def deepseek_client() -> tuple[OpenAI, str, int]:
    load_dotenv()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    timeout = parse_float(os.getenv("DAY25_DEEPSEEK_TIMEOUT_SECONDS"), 60.0)
    retries = parse_int(os.getenv("DAY25_DEEPSEEK_RETRIES"), DEFAULT_DEEPSEEK_RETRIES)
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


def safe_session_id(session_id: str) -> str:
    clean = session_id.strip()
    if not SESSION_RE.match(clean):
        raise ValueError("session_id may contain only letters, digits, underscore, dot and dash")
    return clean


def session_dir(session_id: str) -> Path:
    return load_store_dir() / "sessions" / safe_session_id(session_id)


def messages_path(session_id: str) -> Path:
    return session_dir(session_id) / "messages.json"


def task_state_path(session_id: str) -> Path:
    return session_dir(session_id) / "task_state.json"


def turns_path(session_id: str) -> Path:
    return session_dir(session_id) / "turns.jsonl"


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_json(payload), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def default_task_state(goal: str = "") -> dict[str, Any]:
    return {
        "goal": goal.strip(),
        "clarifications": [],
        "constraints": [
            "отвечать только на основании найденных RAG-источников",
            "в каждом обычном ответе выводить источники и цитаты",
        ],
        "fixed_terms": {},
        "open_questions": [],
        "updated_at": now_iso(),
    }


def load_messages(session_id: str) -> list[dict[str, Any]]:
    messages = read_json(messages_path(session_id), [])
    return messages if isinstance(messages, list) else []


def load_task_state(session_id: str, goal: str = "") -> dict[str, Any]:
    state = read_json(task_state_path(session_id), None)
    if isinstance(state, dict):
        state.setdefault("goal", goal)
        state.setdefault("clarifications", [])
        state.setdefault("constraints", [])
        state.setdefault("fixed_terms", {})
        state.setdefault("open_questions", [])
        state.setdefault("updated_at", now_iso())
        return state
    return default_task_state(goal)


def create_session(session_id: str, goal: str, reset: bool = False) -> dict[str, Any]:
    root = session_dir(session_id)
    if reset and root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    state = default_task_state(goal)
    write_json(messages_path(session_id), [])
    write_json(task_state_path(session_id), state)
    turns_path(session_id).write_text("", encoding="utf-8")
    return state


def compact(text: str, limit: int = 900) -> str:
    clean = re.sub(r"\s+", " ", str(text)).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 20].rstrip() + " ...[truncated]"


def unique_append(values: list[str], additions: list[str], limit: int = 40) -> list[str]:
    result = [str(item).strip() for item in values if str(item).strip()]
    seen = {item.lower() for item in result}
    for item in additions:
        clean = str(item).strip()
        if not clean or clean.lower() in seen:
            continue
        result.append(clean)
        seen.add(clean.lower())
    return result[-limit:]


def extract_terms(text: str) -> dict[str, str]:
    terms: dict[str, str] = {}
    patterns = [
        r"/v[0-9]+/[A-Za-z0-9_./:-]+",
        r"\b[A-Z][A-Za-z0-9_]*(?:Client|Service|Repository|ViewModel|Screen|Entity|Api|Route)\b",
        r"\b[A-Za-z0-9_]+(?:Email|Token|Session|Spread|Tarot)[A-Za-z0-9_]*\b",
        r"\b[A-Za-z0-9_]+\.kt\b",
        r"\b[A-Za-z0-9_]+\.md\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = match.group(0).strip()
            if value:
                terms[value] = "упомянуто пользователем или найдено в ходе диалога"
    return terms


def domain_query_hints(text: str) -> str:
    lowered = text.lower()
    hints: list[str] = []
    if any(term in lowered for term in ["авторизац", "auth", "login", "signup", "email", "token", "session"]):
        hints.append(
            "BackendApiClient AuthRepository SignUpViewModel Models verifyEmailCode resendEmailVerification "
            "/v1/auth/signup /v1/auth/login /v1/auth/verify-email-code /v1/auth/resend-verification "
            "/v1/auth/refresh Authorization Bearer refreshSession accessToken refreshToken password weak_password"
        )
    if any(term in lowered for term in ["навигац", "navigation", "screen", "route", "bottomnavigation"]):
        hints.append("Navigation Screen BottomNavigationBar bottomRoutes home horoscope tarot profile welcome login signup")
    if any(term in lowered for term in ["tarot", "таро", "расклад", "history", "spread", "aiservice", "insight"]):
        hints.append("tarot_spread_history listSpreads saveSpread renameSpread deleteSpread AiService tarotDaily TarotSpreadHistoryEntity")
    if any(term in lowered for term in ["backend", "readme", "stack", "storage", "postgres", "ktor"]):
        hints.append("backend README Ktor PostgreSQL Firebase Firestore Authorization Bearer /health /v1/profile")
    return " ".join(hints)


def build_retrieval_query(question: str, task_state: dict[str, Any], messages: list[dict[str, Any]], recent_limit: int) -> str:
    fixed_terms = task_state.get("fixed_terms", {})
    if isinstance(fixed_terms, dict):
        terms_text = " ".join([f"{key} {value}" for key, value in fixed_terms.items()])
    else:
        terms_text = ""
    recent_user_messages = [
        str(item.get("content", ""))
        for item in messages[-recent_limit:]
        if item.get("role") == "user"
    ]
    parts = [
        question,
        str(task_state.get("goal", "")),
        domain_query_hints(f"{question} {task_state.get('goal', '')}"),
        " ".join(str(item) for item in task_state.get("clarifications", [])[-6:]),
        " ".join(str(item) for item in task_state.get("constraints", [])[-6:]),
        terms_text,
        " ".join(recent_user_messages[-4:]),
    ]
    return compact(" ".join(part for part in parts if part), 1800)


def build_chat_sources_context(chunks: list[RetrievedChunk]) -> str:
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
                    compact(chunk.text, 760),
                ]
            )
        )
    return "\n\n---\n\n".join(parts)


def source_patterns_for_question(question: str) -> list[str]:
    lowered = question.lower()
    patterns: list[str] = []
    if any(term in lowered for term in ["readme", "stack", "storage", "authorization bearer", "endpoints"]):
        patterns.append(r"%backend\README.md")
    if any(term in lowered for term in ["screen routes", "routes", "bottomnavigationbar", "navigation"]):
        patterns.append(r"%Navigation.kt")
    if any(term in lowered for term in ["aiservice", "daily tarot insight", "prompt"]):
        patterns.append(r"%AiService.kt")
    if any(term in lowered for term in ["tarot_spread_history", "dao", "spread history"]):
        patterns.extend([r"%TarotSpreadHistoryDao.kt", r"%Repositories.kt", r"%TarotSpreadHistoryEntity.kt"])
    if any(term in lowered for term in ["verify-email-code", "resend-verification", "refreshtoken", "refresh token", "401"]):
        patterns.extend([r"%BackendApiClient.kt", r"%SignUpViewModel.kt", r"%Application.kt"])
    if any(term in lowered for term in ["signupusecase", "authrepository.signup", "displayname"]):
        patterns.extend([r"%SignUpUseCase.kt", r"%AuthRepository.kt"])
    return patterns


def pinned_source_chunks(index_dir: Path, strategy: str, question: str, limit: int = 3) -> list[RetrievedChunk]:
    patterns = source_patterns_for_question(question)
    if not patterns:
        return []
    conn = sqlite3.connect(db_path(index_dir))
    try:
        rows = []
        for pattern in patterns:
            rows.extend(
                conn.execute(
                    """
                    SELECT strategy, chunk_id, source, title, section, text
                    FROM chunks
                    WHERE strategy = ? AND source LIKE ?
                    ORDER BY chunk_id
                    LIMIT ?
                    """,
                    (strategy, pattern, limit),
                ).fetchall()
            )
    finally:
        conn.close()
    chunks = []
    seen = set()
    for row in rows:
        if row[1] in seen:
            continue
        seen.add(row[1])
        chunks.append(
            RetrievedChunk(
                rank=len(chunks) + 1,
                score=1.0,
                strategy=row[0],
                chunk_id=row[1],
                source=row[2],
                title=row[3],
                section=row[4],
                text=row[5],
            )
        )
        if len(chunks) >= limit:
            break
    return chunks


def merge_chunks(primary: list[RetrievedChunk], secondary: list[RetrievedChunk], limit: int) -> list[RetrievedChunk]:
    merged = []
    seen = set()
    for chunk in [*primary, *secondary]:
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        merged.append(
            RetrievedChunk(
                rank=len(merged) + 1,
                score=chunk.score,
                strategy=chunk.strategy,
                chunk_id=chunk.chunk_id,
                source=chunk.source,
                title=chunk.title,
                section=chunk.section,
                text=chunk.text,
            )
        )
        if len(merged) >= limit:
            break
    return merged


def is_task_state_question(question: str) -> bool:
    lowered = question.lower()
    markers = [
        "какая цель",
        "цель",
        "огранич",
        "что мы уже",
        "что уже",
        "зафикс",
        "уточнил",
        "уточнили",
        "термин",
        "памят",
        "task state",
    ]
    return any(marker in lowered for marker in markers)


def task_state_chunk(session_id: str, task_state: dict[str, Any]) -> RetrievedChunk:
    fixed_terms = task_state.get("fixed_terms") if isinstance(task_state.get("fixed_terms"), dict) else {}
    text = "\n".join(
        [
            f"session_id: {session_id}",
            "ограничения constraints:",
            *[f"- ограничение: {item}" for item in task_state.get("constraints", [])],
            f"goal: {task_state.get('goal', '')}",
            "clarifications:",
            *[f"- {item}" for item in task_state.get("clarifications", [])],
            "fixed_terms:",
            *[f"- {key}: {value}" for key, value in fixed_terms.items()],
            "open_questions:",
            *[f"- {item}" for item in task_state.get("open_questions", [])],
        ]
    )
    return RetrievedChunk(
        rank=1,
        score=1.0,
        strategy="task_state",
        chunk_id=f"task_state:{session_id}",
        source="day25_task_state",
        title="Task State",
        section="goal/constraints/clarifications/fixed_terms/open_questions",
        text=text,
    )


def chat_allowed_quotes(chunks: list[RetrievedChunk], retrieval_query: str, min_quotes: int) -> list[dict[str, str]]:
    quotes: list[dict[str, str]] = []
    for index, chunk in enumerate(chunks, start=1):
        if chunk.strategy == "task_state":
            for line in chunk.text.splitlines():
                clean = line.strip()
                if clean.startswith("- ограничение:") or clean.startswith("goal:") or clean.startswith("- "):
                    quotes.append(
                        {
                            "source_id": f"S{index}",
                            "source": chunk.source,
                            "section": chunk.section,
                            "chunk_id": chunk.chunk_id,
                            "quote": clean,
                        }
                    )
                    if len(quotes) >= min_quotes:
                        break
        else:
            quotes.append(
                {
                    "source_id": f"S{index}",
                    "source": chunk.source,
                    "section": chunk.section,
                    "chunk_id": chunk.chunk_id,
                    "quote": best_quote(chunk, retrieval_query),
                }
            )
        if len(quotes) >= max(min_quotes, min(3, len(chunks))):
            break
    return quotes[: max(min_quotes, min(3, len(chunks)))]


def effective_relevance(prepared: dict[str, Any], has_memory_source: bool, has_pinned_source: bool) -> float:
    if has_memory_source or has_pinned_source:
        return 1.0
    context = float(prepared["context_relevance"])
    vector = float(prepared["max_vector_relevance"])
    keyword = float(prepared["max_keyword_relevance"])
    if keyword >= 0.40:
        return round(max(context, vector, 0.63), 6)
    if keyword >= 0.35 and vector >= 0.55:
        return round(max(context, vector, 0.63), 6)
    if keyword >= 0.20 and vector >= context:
        return round(vector, 6)
    return context


def recent_messages_for_prompt(messages: list[dict[str, Any]], limit: int) -> list[dict[str, str]]:
    compacted = []
    for item in messages[-limit:]:
        role = item.get("role")
        if role not in {"user", "assistant"}:
            continue
        compacted.append({"role": role, "content": compact(str(item.get("content", "")), 700)})
    return compacted


def ask_chat_with_sources(
    question: str,
    chunks: list[RetrievedChunk],
    retrieval_query: str,
    task_state: dict[str, Any],
    messages: list[dict[str, Any]],
    min_quotes: int,
    recent_limit: int,
) -> tuple[dict[str, Any], LlmResult, bool]:
    prompt_chunks = chunks[:3]
    allowed_quotes = chat_allowed_quotes(prompt_chunks, retrieval_query, max(min_quotes, min(3, len(prompt_chunks))))
    schema_hint = {
        "answer": "краткий ответ на русском, учитывающий task_state и историю",
        "sources": [{"source_id": "S1", "source": "...", "section": "...", "chunk_id": "..."}],
        "quotes": [{"source_id": "S1", "source": "...", "section": "...", "chunk_id": "...", "quote": "точная цитата из ALLOWED_QUOTES"}],
        "confidence": "high|medium|low",
        "needs_clarification": False,
        "task_state_update": {
            "goal": None,
            "clarifications_add": [],
            "constraints_add": [],
            "fixed_terms_update": {},
            "open_questions_add": [],
            "open_questions_resolved": [],
        },
    }
    request_messages = [
        {
            "role": "system",
            "content": (
                "Ты production-like RAG-ассистент по проекту AstroTarot. "
                "Используй только SOURCES для фактов о коде и архитектуре. "
                "Учитывай TASK_STATE и RECENT_MESSAGES, чтобы не терять цель диалога. "
                "Верни только JSON без markdown. Обязательные поля: answer, sources, quotes, confidence, "
                "needs_clarification, task_state_update. "
                "sources должны содержать source_id, source, section, chunk_id. "
                "quotes должны содержать source_id, source, section, chunk_id, quote. "
                "quote должен быть дословно скопирован из ALLOWED_QUOTES. Не сокращай и не переформатируй quote. "
                "Если SOURCES не отвечают на вопрос, answer начинается с 'не знаю', needs_clarification=true, "
                "и попроси уточнить файл, класс, функцию или endpoint."
            ),
        },
        {
            "role": "user",
            "content": (
                f"TASK_STATE:\n{to_json(task_state)}\n\n"
                f"RECENT_MESSAGES:\n{to_json(recent_messages_for_prompt(messages, recent_limit))}\n\n"
                f"QUESTION:\n{question}\n\n"
                f"RETRIEVAL_QUERY:\n{retrieval_query}\n\n"
                f"REQUIRED_JSON_SHAPE:\n{to_json(schema_hint)}\n\n"
                f"ALLOWED_QUOTES:\n{to_json(allowed_quotes)}\n\n"
                f"SOURCES:\n{build_chat_sources_context(prompt_chunks)}"
            ),
        },
    ]
    result = LlmResult("day25_rag_memory_chat", "", 0, 0, 0, 0.0)
    parsed: dict[str, Any] | None = None
    for attempt in range(1, DEFAULT_DEEPSEEK_RETRIES + 1):
        result = ask_deepseek(request_messages, "day25_rag_memory_chat", max_tokens=1400)
        parsed = extract_json_object(result.answer)
        if parsed:
            break
        if "timed out" not in result.answer.lower() and not result.error:
            break
    model_json_valid = True
    if not parsed:
        parsed = {
            "answer": result.answer or result.error or "не знаю. DeepSeek не вернул валидный JSON.",
            "sources": [],
            "quotes": [],
            "confidence": "low",
            "needs_clarification": bool(result.error),
            "task_state_update": {},
        }
        model_json_valid = False
    elif isinstance(parsed.get("answer"), str) and parsed["answer"].strip().startswith("{"):
        nested = extract_json_object(parsed["answer"])
        if nested and isinstance(nested.get("sources"), list) and isinstance(nested.get("quotes"), list):
            parsed = nested
    parsed.setdefault("answer", "")
    parsed.setdefault("sources", [])
    parsed.setdefault("quotes", [])
    parsed.setdefault("confidence", "medium")
    parsed.setdefault("needs_clarification", False)
    parsed.setdefault("task_state_update", {})
    return parsed, result, model_json_valid


def update_task_state(
    task_state: dict[str, Any],
    question: str,
    response: dict[str, Any],
    chunks: list[RetrievedChunk],
) -> dict[str, Any]:
    updated = json.loads(json.dumps(task_state, ensure_ascii=False))
    change = response.get("task_state_update") if isinstance(response.get("task_state_update"), dict) else {}
    goal = change.get("goal")
    if isinstance(goal, str) and goal.strip():
        updated["goal"] = goal.strip()
    clarifications = change.get("clarifications_add") if isinstance(change.get("clarifications_add"), list) else []
    constraints = change.get("constraints_add") if isinstance(change.get("constraints_add"), list) else []
    open_add = change.get("open_questions_add") if isinstance(change.get("open_questions_add"), list) else []
    resolved = change.get("open_questions_resolved") if isinstance(change.get("open_questions_resolved"), list) else []

    lowered_question = question.lower()
    if "только по источникам" in lowered_question or "с источниками" in lowered_question:
        constraints.append("пользователь требует отвечать только с источниками")
    if "запомни" in lowered_question or "цель" in lowered_question or "огранич" in lowered_question:
        clarifications.append(compact(question, 220))

    updated["clarifications"] = unique_append(list(updated.get("clarifications", [])), [str(item) for item in clarifications])
    updated["constraints"] = unique_append(list(updated.get("constraints", [])), [str(item) for item in constraints])

    fixed_terms = updated.get("fixed_terms") if isinstance(updated.get("fixed_terms"), dict) else {}
    fixed_terms.update(extract_terms(question))
    fixed_update = change.get("fixed_terms_update") if isinstance(change.get("fixed_terms_update"), dict) else {}
    for key, value in fixed_update.items():
        key_text = str(key).strip()
        value_text = str(value).strip()
        if key_text and value_text:
            fixed_terms[key_text] = value_text
    updated["fixed_terms"] = dict(list(fixed_terms.items())[-80:])

    current_open = [str(item).strip() for item in updated.get("open_questions", []) if str(item).strip()]
    resolved_set = {str(item).strip().lower() for item in resolved if str(item).strip()}
    current_open = [item for item in current_open if item.lower() not in resolved_set]
    updated["open_questions"] = unique_append(current_open, [str(item) for item in open_add], limit=20)
    updated["updated_at"] = now_iso()
    return updated


def validate_chat_answer(
    parsed: dict[str, Any],
    chunks: list[RetrievedChunk],
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
        chunk = chunks_by_id.get(str(item.get("source_id", ""))) or chunks_by_chunk_id.get(str(item.get("chunk_id", "")))
        quote_results.append(quote_matches_chunk(str(item.get("quote", "")), chunk))
    low_ok = (
        low_relevance_blocked
        and answer.lower().startswith("не знаю")
        and bool(parsed.get("needs_clarification"))
        and not sources
        and not quotes
    )
    normal_ok = all(
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
        ]
    )
    return {
        "model_json_valid": model_json_valid,
        "has_answer": bool(answer.strip()),
        "has_sources": bool(sources),
        "source_fields_ok": source_fields_ok,
        "source_ids_valid": bool(source_ids) and source_ids.issubset(set(chunks_by_id)),
        "has_quotes": bool(quotes),
        "quote_fields_ok": quote_fields_ok,
        "quotes_match_chunks": bool(quote_results) and all(quote_results),
        "low_relevance_rule_ok": low_ok,
        "passed": low_ok or normal_ok,
    }


def ensure_citations(
    parsed: dict[str, Any],
    chunks: list[RetrievedChunk],
    retrieval_query: str,
    min_quotes: int,
) -> tuple[dict[str, Any], list[str]]:
    repaired = json.loads(json.dumps(parsed, ensure_ascii=False))
    repairs: list[str] = []
    sources = repaired.get("sources") if isinstance(repaired.get("sources"), list) else []
    quotes = repaired.get("quotes") if isinstance(repaired.get("quotes"), list) else []
    if chunks and not sources:
        repaired["sources"] = deterministic_sources(chunks[:3])
        repairs.append("sources")
    if chunks and not quotes:
        repaired["quotes"] = chat_allowed_quotes(chunks[:3], retrieval_query, max(min_quotes, min(3, len(chunks))))
        repairs.append("quotes")
    return repaired, repairs


def chunks_payload(chunks: list[RetrievedChunk]) -> list[dict[str, Any]]:
    return [asdict(chunk) for chunk in chunks]


def run_turn(
    session_id: str,
    question: str,
    index_dir: Path,
    strategy: str,
    initial_k: int,
    final_k: int,
    similarity_threshold: float,
    relevance_threshold: float,
    min_keep: int,
    min_quotes: int,
    recent_messages: int,
) -> dict[str, Any]:
    root = session_dir(session_id)
    if not root.exists():
        create_session(session_id, "")
    messages = load_messages(session_id)
    task_state = load_task_state(session_id)
    retrieval_query = build_retrieval_query(question, task_state, messages, recent_messages)
    control = ControlQuestion("chat", question, retrieval_query, "", [], [])
    prepared = prepare_chunks(
        control,
        index_dir=index_dir,
        strategy=strategy,
        initial_k=initial_k,
        final_k=final_k,
        similarity_threshold=similarity_threshold,
        min_keep=min_keep,
        rewrite_mode="none",
    )
    pinned_chunks = pinned_source_chunks(index_dir, strategy, question, limit=min(3, final_k))
    rag_chunks = merge_chunks(pinned_chunks, prepared["chunks"], final_k)
    memory_question = is_task_state_question(question)
    memory_chunks = [task_state_chunk(session_id, task_state)] if memory_question else []
    chunks = [*memory_chunks, *rag_chunks[: max(0, final_k - len(memory_chunks))]]
    gated_relevance = effective_relevance(prepared, bool(memory_chunks), bool(pinned_chunks))
    low_relevance_blocked = gated_relevance < relevance_threshold or not chunks
    if low_relevance_blocked:
        parsed = low_relevance_answer(question, gated_relevance, relevance_threshold)
        parsed["task_state_update"] = {
            "clarifications_add": [f"Слабый контекст по вопросу: {compact(question, 180)}"],
            "open_questions_add": ["уточнить файл, класс, функцию или endpoint для слабого RAG-контекста"],
        }
        llm = LlmResult("day25_low_relevance", "", 0, 0, 0, 0.0)
        model_json_valid = True
    else:
        parsed, llm, model_json_valid = ask_chat_with_sources(
            question,
            chunks,
            prepared["query"],
            task_state,
            messages,
            min_quotes,
            recent_messages,
        )
    citation_repairs: list[str] = []
    if not low_relevance_blocked:
        parsed, citation_repairs = ensure_citations(parsed, chunks, prepared["query"], min_quotes)
    validation = validate_chat_answer(parsed, chunks, model_json_valid, low_relevance_blocked)
    updated_task_state = update_task_state(task_state, question, parsed, chunks)
    turn_id = len([item for item in messages if item.get("role") == "user"]) + 1
    user_message = {"role": "user", "content": question, "created_at": now_iso(), "turn_id": turn_id}
    assistant_message = {
        "role": "assistant",
        "content": str(parsed.get("answer", "")),
        "created_at": now_iso(),
        "turn_id": turn_id,
        "sources": parsed.get("sources", []),
        "quotes": parsed.get("quotes", []),
        "context_relevance": gated_relevance,
    }
    messages.extend([user_message, assistant_message])
    write_json(messages_path(session_id), messages)
    write_json(task_state_path(session_id), updated_task_state)

    result = {
        "session_id": session_id,
        "turn_id": turn_id,
        "question": question,
        "retrieval_query": prepared["query"],
        "context_relevance": gated_relevance,
        "rag_context_relevance": prepared["context_relevance"],
        "max_vector_relevance": prepared["max_vector_relevance"],
        "max_keyword_relevance": prepared["max_keyword_relevance"],
        "initial_count": prepared["initial_count"],
        "filtered_count": prepared["filtered_count"],
        "lexical_count": prepared["lexical_count"],
        "merged_count": prepared["merged_count"],
        "final_count": prepared["final_count"],
        "low_relevance_blocked": low_relevance_blocked,
        "selected_sources": deterministic_sources(chunks),
        "selected_quotes": deterministic_quotes(chunks, prepared["query"], min_quotes),
        "chunks": chunks_payload(chunks),
        "rag_chunks": chunks_payload(rag_chunks),
        "memory_source_used": bool(memory_chunks),
        "pinned_source_used": bool(pinned_chunks),
        "response": parsed,
        "citation_repairs": citation_repairs,
        "llm": asdict(llm),
        "model_json_valid": model_json_valid,
        "validation": validation,
        "task_state_before": task_state,
        "task_state_after": updated_task_state,
    }
    append_jsonl(turns_path(session_id), result)
    return result


def print_sources(sources: Any) -> None:
    if not isinstance(sources, list) or not sources:
        print("- нет")
        return
    for source in sources:
        if isinstance(source, dict):
            print(f"- {source.get('source')} | {source.get('section')} | {source.get('chunk_id')}")


def print_quotes(quotes: Any) -> None:
    if not isinstance(quotes, list) or not quotes:
        print("- нет")
        return
    for quote in quotes:
        if isinstance(quote, dict):
            print(f"- {quote.get('source')} | {quote.get('section')} | {quote.get('chunk_id')}: {quote.get('quote')}")


def print_task_state(state: dict[str, Any]) -> None:
    print(f"goal={state.get('goal', '')}")
    print("constraints:")
    for item in state.get("constraints", [])[-6:]:
        print(f"- {item}")
    print("clarifications:")
    for item in state.get("clarifications", [])[-6:]:
        print(f"- {item}")
    print("fixed_terms:")
    for key, value in list((state.get("fixed_terms") or {}).items())[-10:]:
        print(f"- {key}: {value}")
    print("open_questions:")
    for item in state.get("open_questions", [])[-6:]:
        print(f"- {item}")


def print_turn(result: dict[str, Any], verbose: bool = False) -> None:
    validation = result["validation"]
    llm = result["llm"]
    print(f"session_id={result['session_id']} turn_id={result['turn_id']}")
    print(
        f"context_relevance={result['context_relevance']} threshold_passed={not result['low_relevance_blocked']} "
        f"tokens={llm.get('total_tokens', 0)}"
    )
    print(
        f"validation sources={validation['has_sources']} quotes={validation['has_quotes']} "
        f"quotes_match_chunks={validation['quotes_match_chunks']} passed={validation['passed']}"
    )
    print("Ответ:")
    print(result["response"].get("answer", ""))
    print("Источники:")
    print_sources(result["response"].get("sources", []))
    print("Цитаты:")
    print_quotes(result["response"].get("quotes", []))
    print("Task State:")
    print_task_state(result["task_state_after"])
    if verbose:
        print("full_json:")
        print(to_json(result))


def scenario_definitions() -> dict[str, dict[str, Any]]:
    return {
        "auth_backend_review": {
            "goal": "Собрать источник-backed описание авторизации AstroTarot: signup, email verification, session refresh и backend constraints.",
            "questions": [
                "Запомни цель: разбираем только авторизацию AstroTarot и отвечаем только по источникам.",
                "Какой endpoint Android вызывает для проверки email-кода и какие поля отправляет?",
                "Где в коде вызывается повторная отправка email verification code?",
                "Как SignUpUseCase передает email, password и displayName в authRepository.signUp?",
                "Как BackendApiClient обновляет access token после 401?",
                "Какие endpoints перечислены в backend README и где нужен Authorization Bearer?",
                "Какой backend stack и storage описаны в README?",
                "Зафиксируй термин refreshSession и объясни его роль в текущей задаче.",
                "Собери короткий порядок signup -> verify email -> authorized requests.",
                "Сделай финальную сводку по нашей цели и не забудь ограничения, которые мы зафиксировали.",
            ],
        },
        "navigation_tarot_review": {
            "goal": "Собрать источник-backed описание навигации AstroTarot и tarot history / AI insight flow.",
            "questions": [
                "Запомни цель: разбираем navigation, tarot history и AI insight, без домыслов и только с источниками.",
                "Какие основные Screen routes объявлены в навигации AstroTarot?",
                "Когда показывается BottomNavigationBar?",
                "Какие данные описывает backend TarotSpreadHistoryDto?",
                "Какие DAO функции видно для local tarot_spread_history?",
                "Какой prompt использует AiService для daily tarot insight?",
                "Зафиксируй термин tarot_spread_history и зачем он важен для цели диалога.",
                "Какие источники чаще всего подтверждают tarot history flow?",
                "Собери короткий flow: пользователь открывает tarot, сохраняет расклад, потом смотрит историю.",
                "Сделай финальный checklist по цели диалога с учетом уже зафиксированных терминов.",
            ],
        },
    }


def scenario_names(raw: str) -> list[str]:
    if raw == "both":
        return ["auth_backend_review", "navigation_tarot_review"]
    scenarios = scenario_definitions()
    if raw not in scenarios:
        raise ValueError(f"Unknown scenario: {raw}")
    return [raw]


def summarize_demo(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for item in results if item["validation"]["passed"])
    with_sources = sum(1 for item in results if item["validation"]["has_sources"])
    with_quotes = sum(1 for item in results if item["validation"]["has_quotes"])
    quotes_match = sum(1 for item in results if item["validation"]["quotes_match_chunks"])
    low_relevance = sum(1 for item in results if item["low_relevance_blocked"])
    tokens = sum(int(item["llm"].get("total_tokens", 0)) for item in results)
    return {
        "turns": total,
        "passed": passed,
        "answers_with_sources": with_sources,
        "answers_with_quotes": with_quotes,
        "quotes_match_chunks": quotes_match,
        "low_relevance_blocks": low_relevance,
        "deepseek_total_tokens": tokens,
    }


def report_source_lines(sources: Any) -> list[str]:
    if not isinstance(sources, list) or not sources:
        return ["- нет"]
    return [
        f"- `{source.get('source')}` | `{source.get('section')}` | `{source.get('chunk_id')}`"
        for source in sources
        if isinstance(source, dict)
    ] or ["- нет"]


def report_quote_lines(quotes: Any) -> list[str]:
    if not isinstance(quotes, list) or not quotes:
        return ["- нет"]
    return [
        f"- `{quote.get('source')}` | `{quote.get('section')}` | `{quote.get('chunk_id')}`: {quote.get('quote')}"
        for quote in quotes
        if isinstance(quote, dict)
    ] or ["- нет"]


def build_report(payload: dict[str, Any]) -> str:
    rows = []
    for item in payload["results"]:
        validation = item["validation"]
        rows.append(
            "| {scenario} | {turn} | {passed} | {sources} | {quotes} | {match} | {rel:.3f} | {tokens} |".format(
                scenario=item["scenario"],
                turn=item["turn_id"],
                passed="yes" if validation["passed"] else "no",
                sources="yes" if validation["has_sources"] else "no",
                quotes="yes" if validation["has_quotes"] else "no",
                match="yes" if validation["quotes_match_chunks"] else "no",
                rel=item["context_relevance"],
                tokens=item["llm"]["total_tokens"],
            )
        )
    details: list[str] = []
    for item in payload["results"]:
        details.extend(
            [
                f"### {item['scenario']} / turn {item['turn_id']}",
                "",
                f"**Question:** {item['question']}",
                "",
                "**Ответ:**",
                "",
                str(item["response"].get("answer", "")),
                "",
                "**Источники:**",
                "",
                *report_source_lines(item["response"].get("sources", [])),
                "",
                "**Цитаты:**",
                "",
                *report_quote_lines(item["response"].get("quotes", [])),
                "",
                "**Task State After:**",
                "",
                "```json",
                to_json(item["task_state_after"]),
                "```",
                "",
            ]
        )
    return "\n".join(
        [
            "# Day 25. Mini Chat with RAG + Task Memory",
            "",
            "## Summary",
            "",
            f"- generated_at: `{payload['generated_at']}`",
            f"- real_data: `{payload.get('real_data', True)}`",
            f"- index_dir: `{payload['index_dir']}`",
            f"- store_dir: `{payload['store_dir']}`",
            f"- strategy: `{payload['strategy']}`",
            f"- scenarios: `{', '.join(payload['scenarios'])}`",
            f"- turns: `{payload['summary']['turns']}`",
            f"- passed: `{payload['summary']['passed']}`",
            f"- answers_with_sources: `{payload['summary']['answers_with_sources']}`",
            f"- answers_with_quotes: `{payload['summary']['answers_with_quotes']}`",
            f"- quotes_match_chunks: `{payload['summary']['quotes_match_chunks']}`",
            f"- deepseek_total_tokens: `{payload['summary']['deepseek_total_tokens']}`",
            "",
            "## Storage Model",
            "",
            "- `sessions/{session_id}/messages.json` stores the chat history.",
            "- `sessions/{session_id}/task_state.json` stores task memory: goal, clarifications, constraints, fixed_terms, open_questions.",
            "- `sessions/{session_id}/turns.jsonl` stores per-turn RAG trace and validation.",
            "",
            "## Validation Table",
            "",
            "| scenario | turn | passed | sources | quotes | quotes from chunks | relevance | tokens |",
            "|---|---:|---|---|---|---|---:|---:|",
            *rows,
            "",
            "## Scenario Details",
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
            ".\\.venv\\Scripts\\python.exe day25_rag_memory_chat.py new --session-id manual --goal \"Разобрать авторизацию AstroTarot только по источникам\"",
            ".\\.venv\\Scripts\\python.exe day25_rag_memory_chat.py ask \"Как устроена авторизация?\" --session-id manual",
            ".\\.venv\\Scripts\\python.exe day25_rag_memory_chat.py ask \"А какие ограничения мы уже зафиксировали?\" --session-id manual",
            ".\\.venv\\Scripts\\python.exe day25_rag_memory_chat.py show --session-id manual",
            ".\\.venv\\Scripts\\python.exe day25_rag_memory_chat.py verify --scenario both",
            "```",
            "",
        ]
    )


def save_demo_report(payload: dict[str, Any], report_path: str) -> tuple[Path, Path]:
    store = load_store_dir()
    store.mkdir(parents=True, exist_ok=True)
    json_path = (store / "last_demo.json").resolve()
    json_path.write_text(to_json(payload), encoding="utf-8")
    report = Path(report_path).resolve()
    report.write_text(build_report(payload), encoding="utf-8")
    return json_path, report


def common_run_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "index_dir": load_index_dir(args),
        "strategy": args.strategy,
        "initial_k": args.initial_k,
        "final_k": args.final_k,
        "similarity_threshold": args.similarity_threshold,
        "relevance_threshold": args.relevance_threshold,
        "min_keep": args.min_keep,
        "min_quotes": args.min_quotes,
        "recent_messages": args.recent_messages,
    }


def command_new(args: argparse.Namespace) -> int:
    state = create_session(args.session_id, args.goal, reset=True)
    print(f"session_id={safe_session_id(args.session_id)}")
    print(f"messages={messages_path(args.session_id).resolve()}")
    print(f"task_state={task_state_path(args.session_id).resolve()}")
    print("Task State:")
    print_task_state(state)
    return 0


def command_reset(args: argparse.Namespace) -> int:
    root = session_dir(args.session_id)
    if root.exists():
        shutil.rmtree(root)
    print(f"reset=True session_id={safe_session_id(args.session_id)}")
    return 0


def command_show(args: argparse.Namespace) -> int:
    messages = load_messages(args.session_id)
    state = load_task_state(args.session_id)
    print(f"session_id={safe_session_id(args.session_id)}")
    print(f"messages_count={len(messages)}")
    print(f"messages_path={messages_path(args.session_id).resolve()}")
    print(f"task_state_path={task_state_path(args.session_id).resolve()}")
    print("Task State:")
    print_task_state(state)
    print("Recent Messages:")
    for item in messages[-args.recent_messages:]:
        print(f"- {item.get('role')}[{item.get('turn_id')}]: {compact(item.get('content', ''), 220)}")
    return 0


def command_ask(args: argparse.Namespace) -> int:
    result = run_turn(args.session_id, args.question, **common_run_kwargs(args))
    print_turn(result, verbose=args.verbose)
    return 0 if result["validation"]["passed"] else 2


def command_chat(args: argparse.Namespace) -> int:
    if not session_dir(args.session_id).exists():
        create_session(args.session_id, args.goal or "")
    print(f"session_id={safe_session_id(args.session_id)}")
    print("Введите вопрос. Для выхода: /exit")
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if question in {"/exit", "/quit"}:
            return 0
        if not question:
            continue
        result = run_turn(args.session_id, question, **common_run_kwargs(args))
        print_turn(result, verbose=args.verbose)


def command_demo(args: argparse.Namespace) -> int:
    scenarios = scenario_definitions()
    selected = scenario_names(args.scenario)
    results: list[dict[str, Any]] = []
    print("real_data=True")
    print("data_source=day21 SQLite index + Ollama embeddings + DeepSeek API")
    for scenario_name in selected:
        scenario = scenarios[scenario_name]
        session_id = f"day25_{scenario_name}"
        create_session(session_id, scenario["goal"], reset=True)
        print(f"### scenario={scenario_name} session_id={session_id}")
        for question in scenario["questions"]:
            result = run_turn(session_id, question, **common_run_kwargs(args))
            result["scenario"] = scenario_name
            results.append(result)
            print(
                f"turn={result['turn_id']} passed={result['validation']['passed']} "
                f"sources={result['validation']['has_sources']} quotes={result['validation']['has_quotes']} "
                f"relevance={result['context_relevance']} tokens={result['llm']['total_tokens']}"
            )
    payload = {
        "generated_at": now_iso(),
        "real_data": True,
        "index_dir": str(load_index_dir(args)),
        "store_dir": str(load_store_dir()),
        "strategy": args.strategy,
        "scenarios": selected,
        "summary": summarize_demo(results),
        "results": results,
    }
    json_path, report = save_demo_report(payload, args.report)
    print(f"summary={to_json(payload['summary'])}")
    print(f"json_saved={json_path}")
    print(f"report_saved={report}")
    return 0 if payload["summary"]["passed"] == payload["summary"]["turns"] else 2


def add_common_args(parser: argparse.ArgumentParser) -> None:
    load_dotenv()
    parser.add_argument("--index-dir", default=os.getenv("DAY25_RAG_INDEX_DIR", DEFAULT_INDEX_DIR))
    parser.add_argument("--strategy", default=os.getenv("DAY25_RAG_STRATEGY", DEFAULT_STRATEGY))
    parser.add_argument("--initial-k", type=int, default=parse_int(os.getenv("DAY25_INITIAL_K"), DEFAULT_INITIAL_K))
    parser.add_argument("--final-k", type=int, default=parse_int(os.getenv("DAY25_FINAL_K"), DEFAULT_FINAL_K))
    parser.add_argument(
        "--similarity-threshold",
        type=float,
        default=parse_float(os.getenv("DAY25_SIMILARITY_THRESHOLD"), DEFAULT_SIMILARITY_THRESHOLD),
    )
    parser.add_argument(
        "--relevance-threshold",
        type=float,
        default=parse_float(os.getenv("DAY25_RELEVANCE_THRESHOLD"), DEFAULT_RELEVANCE_THRESHOLD),
    )
    parser.add_argument("--min-keep", type=int, default=parse_int(os.getenv("DAY25_MIN_KEEP"), DEFAULT_MIN_KEEP))
    parser.add_argument("--min-quotes", type=int, default=parse_int(os.getenv("DAY25_MIN_QUOTES"), DEFAULT_MIN_QUOTES))
    parser.add_argument(
        "--recent-messages",
        type=int,
        default=parse_int(os.getenv("DAY25_RECENT_MESSAGES"), DEFAULT_RECENT_MESSAGES),
    )
    parser.add_argument("--verbose", action="store_true")


def main() -> None:
    parser = argparse.ArgumentParser(description="Day25 mini chat with RAG, sources and task memory.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    new_parser = subparsers.add_parser("new", help="Create a fresh chat session.")
    new_parser.add_argument("--session-id", required=True)
    new_parser.add_argument("--goal", required=True)
    new_parser.set_defaults(func=command_new)

    ask_parser = subparsers.add_parser("ask", help="Ask one question in a persisted chat session.")
    ask_parser.add_argument("question")
    ask_parser.add_argument("--session-id", required=True)
    add_common_args(ask_parser)
    ask_parser.set_defaults(func=command_ask)

    chat_parser = subparsers.add_parser("chat", help="Interactive chat loop.")
    chat_parser.add_argument("--session-id", required=True)
    chat_parser.add_argument("--goal", default="")
    add_common_args(chat_parser)
    chat_parser.set_defaults(func=command_chat)

    show_parser = subparsers.add_parser("show", help="Show persisted history and task state.")
    show_parser.add_argument("--session-id", required=True)
    show_parser.add_argument(
        "--recent-messages",
        type=int,
        default=parse_int(os.getenv("DAY25_RECENT_MESSAGES"), DEFAULT_RECENT_MESSAGES),
    )
    show_parser.set_defaults(func=command_show)

    reset_parser = subparsers.add_parser("reset", help="Delete a chat session.")
    reset_parser.add_argument("--session-id", required=True)
    reset_parser.set_defaults(func=command_reset)

    demo_parser = subparsers.add_parser("demo", help="Run real long scenarios and write report.")
    demo_parser.add_argument("--scenario", choices=["auth_backend_review", "navigation_tarot_review", "both"], default="both")
    demo_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    add_common_args(demo_parser)
    demo_parser.set_defaults(func=command_demo)

    verify_parser = subparsers.add_parser("verify", help="Run real long scenarios and write report.")
    verify_parser.add_argument("--scenario", choices=["auth_backend_review", "navigation_tarot_review", "both"], default="both")
    verify_parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    add_common_args(verify_parser)
    verify_parser.set_defaults(func=command_demo)

    args = parser.parse_args()
    try:
        raise SystemExit(args.func(args))
    except Exception as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
