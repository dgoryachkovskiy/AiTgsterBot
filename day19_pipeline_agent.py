import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI


DEFAULT_MCP_URL = "http://138.16.168.37:8004/mcp"
DEFAULT_REPORT_PATH = "DAY19_MCP_PIPELINE_REPORT.md"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
EXPECTED_TOOLS = {
    "search_worldcup_matches",
    "summarize_worldcup_matches",
    "save_worldcup_summary",
    "run_worldcup_pipeline",
}


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class CallResult:
    server_url: str
    tool_name: str
    result: dict[str, Any]
    elapsed_seconds: float


@dataclass(frozen=True)
class AgentResult:
    answer: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    elapsed_seconds: float
    attempts: int
    error: str = ""


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


def load_config(args: argparse.Namespace) -> tuple[str, float]:
    load_dotenv()
    url = args.url or os.getenv("DAY19_MCP_URL", DEFAULT_MCP_URL)
    timeout = args.timeout or parse_float(os.getenv("DAY19_MCP_TIMEOUT_SECONDS"), 20.0)
    return url, timeout


def model_to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, exclude_none=True)
    if hasattr(value, "dict"):
        return value.dict(by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        return value
    return dict(getattr(value, "__dict__", {}))


def normalize_tool(tool: Any) -> ToolInfo:
    raw = model_to_dict(tool)
    return ToolInfo(
        name=raw.get("name") or getattr(tool, "name", ""),
        description=raw.get("description") or getattr(tool, "description", "") or "",
        input_schema=raw.get("inputSchema") or raw.get("input_schema") or {},
    )


def extract_json_from_call_result(call_result: Any) -> dict[str, Any]:
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


async def list_remote_tools(url: str, timeout_seconds: float) -> list[ToolInfo]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def run_session() -> list[ToolInfo]:
        async with streamable_http_client(url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_response = await session.list_tools()
                return [normalize_tool(tool) for tool in tools_response.tools]

    return await asyncio.wait_for(run_session(), timeout=timeout_seconds)


async def call_remote_tool(url: str, timeout_seconds: float, tool_name: str, arguments: dict[str, Any]) -> CallResult:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    started = time.perf_counter()

    async def run_session() -> dict[str, Any]:
        async with streamable_http_client(url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                call_result = await session.call_tool(tool_name, arguments)
                return extract_json_from_call_result(call_result)

    result = await asyncio.wait_for(run_session(), timeout=timeout_seconds)
    return CallResult(url, tool_name, result, time.perf_counter() - started)


def deepseek_client_from_env() -> tuple[OpenAI, str, int]:
    load_dotenv()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    timeout = parse_float(os.getenv("MEMORY_DEEPSEEK_TIMEOUT_SECONDS"), 30.0)
    max_attempts = parse_int(os.getenv("MEMORY_DEEPSEEK_RETRIES"), 2)
    client = OpenAI(api_key=require_env("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL, timeout=timeout, max_retries=0)
    return client, model, max_attempts


def ask_deepseek(pipeline_result: dict[str, Any]) -> AgentResult:
    client, model, max_attempts = deepseek_client_from_env()
    messages = [
        {
            "role": "system",
            "content": (
                "Ты агент, который объясняет результат MCP pipeline. "
                "Используй только переданный JSON. Ответь по-русски, коротко: какие tools были вызваны, "
                "как данные прошли по цепочке, где сохранен результат и какая футбольная сводка получилась."
            ),
        },
        {
            "role": "user",
            "content": f"MCP pipeline result JSON:\n{to_json(pipeline_result)}",
        },
    ]
    started = time.perf_counter()
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                temperature=0,
                max_tokens=700,
                messages=messages,
            )
            usage = response.usage
            answer = response.choices[0].message.content.strip() if response.choices else ""
            return AgentResult(
                answer=answer,
                prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
                total_tokens=getattr(usage, "total_tokens", 0) if usage else 0,
                elapsed_seconds=time.perf_counter() - started,
                attempts=attempt,
            )
        except APIStatusError as error:
            if (error.status_code not in {408, 429} and error.status_code < 500) or attempt == max_attempts:
                return AgentResult("", 0, 0, 0, time.perf_counter() - started, attempt, str(error))
        except (APITimeoutError, APIConnectionError, APIError) as error:
            if attempt == max_attempts:
                return AgentResult("", 0, 0, 0, time.perf_counter() - started, attempt, str(error))
        time.sleep(min(2 * attempt, 6))
    return AgentResult("", 0, 0, 0, time.perf_counter() - started, max_attempts, "DeepSeek request failed")


def indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def format_tools(url: str, tools: list[ToolInfo]) -> str:
    names = {tool.name for tool in tools}
    lines = [
        f"server_url={url}",
        "connected=True",
        f"tools_count={len(tools)}",
        f"expected_tools_present={EXPECTED_TOOLS <= names}",
    ]
    for tool in tools:
        lines.extend(["", f"- name: {tool.name}", f"  description: {tool.description}", "  input_schema:", indent(to_json(tool.input_schema), "    ")])
    return "\n".join(lines)


def chain_arguments(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "date_from": args.date_from,
        "date_to": args.date_to,
        "query": args.query,
        "force_refresh": args.force_refresh,
        "limit": args.limit,
    }


def compact_chain_result(search: CallResult, summary: CallResult, saved: CallResult) -> dict[str, Any]:
    pipeline_id = search.result.get("pipeline_id")
    return {
        "mode": "manual_chain",
        "pipeline_id": pipeline_id,
        "transfer_ok": bool(
            pipeline_id
            and summary.result.get("source_pipeline_id") == pipeline_id
            and saved.result.get("pipeline_id") == pipeline_id
        ),
        "steps": [
            {"tool": search.tool_name, "pipeline_id": pipeline_id, "elapsed_seconds": round(search.elapsed_seconds, 2)},
            {
                "tool": summary.tool_name,
                "source_pipeline_id": summary.result.get("source_pipeline_id"),
                "elapsed_seconds": round(summary.elapsed_seconds, 2),
            },
            {
                "tool": saved.tool_name,
                "pipeline_id": saved.result.get("pipeline_id"),
                "saved_json": saved.result.get("saved_json"),
                "saved_markdown": saved.result.get("saved_markdown"),
                "elapsed_seconds": round(saved.elapsed_seconds, 2),
            },
        ],
        "aggregate": summary.result.get("aggregate"),
        "summary_text": summary.result.get("summary_text"),
        "saved": saved.result,
    }


def format_chain_result(result: dict[str, Any]) -> str:
    saved = result.get("saved") or {}
    aggregate = result.get("aggregate") or {}
    return "\n".join(
        [
            f"mode={result.get('mode')}",
            f"pipeline_id={result.get('pipeline_id')}",
            f"transfer_ok={result.get('transfer_ok')}",
            "steps=search_worldcup_matches -> summarize_worldcup_matches -> save_worldcup_summary",
            f"total_matches={aggregate.get('total_matches')}",
            f"completed={aggregate.get('completed_matches')}",
            f"live={aggregate.get('live_matches')}",
            f"scheduled={aggregate.get('scheduled_matches')}",
            f"saved_json={saved.get('saved_json')}",
            f"saved_markdown={saved.get('saved_markdown')}",
            "summary_text:",
            str(result.get("summary_text") or ""),
        ]
    )


def compact_pipeline_result(call: CallResult) -> dict[str, Any]:
    result = call.result
    summary = result.get("summary") or {}
    saved = result.get("saved") or {}
    return {
        "mode": "automatic_pipeline_tool",
        "tool": call.tool_name,
        "pipeline_id": result.get("pipeline_id"),
        "automatic_chain": result.get("automatic_chain"),
        "transfer_ok": bool(result.get("pipeline_id") and saved.get("pipeline_id") == result.get("pipeline_id")),
        "aggregate": summary.get("aggregate"),
        "summary_text": summary.get("summary_text"),
        "saved": saved,
        "elapsed_seconds": round(call.elapsed_seconds, 2),
    }


def build_report(url: str, tools: list[ToolInfo], result: dict[str, Any], agent: AgentResult | None = None) -> str:
    agent = agent or AgentResult("", 0, 0, 0, 0, 0)
    return "\n".join(
        [
            "# Day 19. MCP Tool Composition Pipeline",
            "",
            "## What Was Built",
            "",
            "- Remote MCP server exposes separate search, summarize, and save tools.",
            "- Agent can call the tools as a manual chain or call the orchestrator pipeline tool.",
            "- Data source is real ESPN FIFA World Cup scoreboard data.",
            "",
            "## Remote MCP",
            "",
            f"- url: `{url}`",
            "- connected: `True`",
            f"- generated_at: `{now_iso()}`",
            f"- tools_count: `{len(tools)}`",
            "",
            "## Tools",
            "",
            "```json",
            to_json([tool.__dict__ for tool in tools]),
            "```",
            "",
            "## Pipeline Result",
            "",
            "```json",
            to_json(result),
            "```",
            "",
            "## DeepSeek Agent Answer",
            "",
            f"- used: `{bool(agent.answer)}`",
            f"- error: `{agent.error or 'none'}`",
            f"- tokens: prompt={agent.prompt_tokens}, completion={agent.completion_tokens}, total={agent.total_tokens}",
            "",
            "```text",
            agent.answer,
            "```",
            "",
            "## Check Commands",
            "",
            "```powershell",
            ".\\.venv\\Scripts\\python.exe day19_pipeline_agent.py tools --url http://138.16.168.37:8004/mcp",
            ".\\.venv\\Scripts\\python.exe day19_pipeline_agent.py chain --force-refresh --url http://138.16.168.37:8004/mcp",
            ".\\.venv\\Scripts\\python.exe day19_pipeline_agent.py pipeline --force-refresh --url http://138.16.168.37:8004/mcp",
            "```",
        ]
    )


def write_report(path: str, url: str, tools: list[ToolInfo], result: dict[str, Any], agent: AgentResult | None = None) -> None:
    Path(path).write_text(build_report(url, tools, result, agent), encoding="utf-8")


def compact_error(error: BaseException) -> str:
    if isinstance(error, BaseExceptionGroup) and error.exceptions:
        return compact_error(error.exceptions[0])
    return f"{type(error).__name__}: {error}"


def run_or_print_error(coro: Any) -> int:
    try:
        return asyncio.run(coro)
    except (Exception, BaseExceptionGroup) as error:
        print("connected=False")
        print(f"error={compact_error(error)}")
        return 1


def add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", default=None, help="Remote MCP Streamable HTTP URL")
    parser.add_argument("--timeout", type=float, default=None, help="MCP timeout in seconds")


def add_pipeline_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--date-from", default=None, help="Optional start date YYYY-MM-DD")
    parser.add_argument("--date-to", default=None, help="Optional end date YYYY-MM-DD")
    parser.add_argument("--query", default=None, help="Optional team, venue, group, or match text filter")
    parser.add_argument("--force-refresh", action="store_true", help="Fetch real data now")
    parser.add_argument("--limit", type=int, default=50, help="Maximum matches")
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH, help="Markdown report path")


async def run_tools(args: argparse.Namespace) -> int:
    url, timeout = load_config(args)
    tools = await list_remote_tools(url, timeout)
    print(format_tools(url, tools))
    names = {tool.name for tool in tools}
    return 0 if EXPECTED_TOOLS <= names else 1


async def run_chain(args: argparse.Namespace) -> int:
    url, timeout = load_config(args)
    tools = await list_remote_tools(url, timeout)
    search = await call_remote_tool(url, timeout, "search_worldcup_matches", chain_arguments(args))
    pipeline_id = search.result["pipeline_id"]
    summary = await call_remote_tool(url, timeout, "summarize_worldcup_matches", {"pipeline_id": pipeline_id, "style": "short_ru"})
    saved = await call_remote_tool(url, timeout, "save_worldcup_summary", {"pipeline_id": pipeline_id, "filename_prefix": "worldcup_pipeline"})
    result = compact_chain_result(search, summary, saved)
    print(format_chain_result(result))
    write_report(args.report, url, tools, result)
    print(f"report_saved:{Path(args.report).resolve()}")
    return 0 if result["transfer_ok"] else 1


async def run_pipeline(args: argparse.Namespace) -> int:
    url, timeout = load_config(args)
    tools = await list_remote_tools(url, timeout)
    call = await call_remote_tool(
        url,
        timeout,
        "run_worldcup_pipeline",
        {
            **chain_arguments(args),
            "style": "short_ru",
            "filename_prefix": "worldcup_pipeline",
        },
    )
    result = compact_pipeline_result(call)
    print(format_chain_result(result))
    print(f"automatic_chain={result.get('automatic_chain')}")
    write_report(args.report, url, tools, result)
    print(f"report_saved:{Path(args.report).resolve()}")
    return 0 if result["transfer_ok"] else 1


async def run_ask(args: argparse.Namespace) -> int:
    url, timeout = load_config(args)
    tools = await list_remote_tools(url, timeout)
    call = await call_remote_tool(
        url,
        timeout,
        "run_worldcup_pipeline",
        {
            **chain_arguments(args),
            "style": "short_ru",
            "filename_prefix": "worldcup_pipeline",
        },
    )
    result = compact_pipeline_result(call)
    print(format_chain_result(result))
    print("")
    agent = ask_deepseek(result)
    if agent.error:
        print(f"deepseek_error={agent.error}")
    else:
        print(f"deepseek_used=True tokens prompt={agent.prompt_tokens} completion={agent.completion_tokens} total={agent.total_tokens}")
        print("agent_answer:")
        print(agent.answer)
    write_report(args.report, url, tools, result, agent)
    print(f"report_saved:{Path(args.report).resolve()}")
    return 0 if not agent.error and result["transfer_ok"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Day 19 agent for composed MCP tool pipelines.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    tools_parser = subparsers.add_parser("tools", help="list remote MCP tools")
    add_connection_args(tools_parser)

    chain_parser = subparsers.add_parser("chain", help="call search -> summarize -> save as separate MCP tools")
    add_connection_args(chain_parser)
    add_pipeline_args(chain_parser)

    pipeline_parser = subparsers.add_parser("pipeline", help="call the single automatic pipeline MCP tool")
    add_connection_args(pipeline_parser)
    add_pipeline_args(pipeline_parser)

    ask_parser = subparsers.add_parser("ask", help="run automatic MCP pipeline and ask DeepSeek to explain it")
    add_connection_args(ask_parser)
    add_pipeline_args(ask_parser)

    args = parser.parse_args()
    if args.command == "tools":
        raise SystemExit(run_or_print_error(run_tools(args)))
    if args.command == "chain":
        raise SystemExit(run_or_print_error(run_chain(args)))
    if args.command == "pipeline":
        raise SystemExit(run_or_print_error(run_pipeline(args)))
    if args.command == "ask":
        raise SystemExit(run_or_print_error(run_ask(args)))


if __name__ == "__main__":
    main()
