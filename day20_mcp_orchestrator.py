import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI


DEFAULT_GITHUB_MCP_URL = "http://138.16.168.37:8000/mcp"
DEFAULT_WORLDCUP_SCHEDULER_MCP_URL = "http://138.16.168.37:8002/mcp"
DEFAULT_WORLDCUP_PIPELINE_MCP_URL = "http://138.16.168.37:8004/mcp"
DEFAULT_REPORT_PATH = "DAY20_MCP_ORCHESTRATION_REPORT.md"
DEFAULT_STORE_DIR = "day20_orchestration_store"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
THINKING_DISABLED = {"thinking": {"type": "disabled"}}
DEFAULT_ROUTE = [
    ("github", "get_github_repo_summary"),
    ("worldcup_scheduler", "get_worldcup_match_summary"),
    ("worldcup_pipeline", "run_worldcup_pipeline"),
]


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class ServerConfig:
    server_id: str
    url: str


@dataclass(frozen=True)
class ToolInfo:
    server_id: str
    server_url: str
    tool_name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ServerStatus:
    server_id: str
    server_url: str
    connected: bool
    tools_count: int
    error: str = ""


@dataclass(frozen=True)
class RouteStep:
    server_id: str
    tool_name: str
    arguments: dict[str, Any]
    reason: str


@dataclass(frozen=True)
class ToolCallTrace:
    step: int
    server_id: str
    server_url: str
    tool_name: str
    arguments: dict[str, Any]
    ok: bool
    elapsed_seconds: float
    result: dict[str, Any]
    error: str = ""


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


def load_servers() -> list[ServerConfig]:
    load_dotenv()
    return [
        ServerConfig("github", os.getenv("DAY20_GITHUB_MCP_URL", DEFAULT_GITHUB_MCP_URL)),
        ServerConfig("worldcup_scheduler", os.getenv("DAY20_WORLDCUP_SCHEDULER_MCP_URL", DEFAULT_WORLDCUP_SCHEDULER_MCP_URL)),
        ServerConfig("worldcup_pipeline", os.getenv("DAY20_WORLDCUP_PIPELINE_MCP_URL", DEFAULT_WORLDCUP_PIPELINE_MCP_URL)),
    ]


def timeout_from_env(args: argparse.Namespace) -> float:
    load_dotenv()
    return args.timeout or parse_float(os.getenv("DAY20_MCP_TIMEOUT_SECONDS"), 20.0)


def store_dir() -> Path:
    load_dotenv()
    return Path(os.getenv("DAY20_ORCHESTRATION_STORE_DIR", DEFAULT_STORE_DIR))


def model_to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, exclude_none=True)
    if hasattr(value, "dict"):
        return value.dict(by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        return value
    return dict(getattr(value, "__dict__", {}))


def normalize_tool(server: ServerConfig, tool: Any) -> ToolInfo:
    raw = model_to_dict(tool)
    return ToolInfo(
        server_id=server.server_id,
        server_url=server.url,
        tool_name=raw.get("name") or getattr(tool, "name", ""),
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


def compact_error(error: BaseException) -> str:
    if isinstance(error, BaseExceptionGroup) and error.exceptions:
        return compact_error(error.exceptions[0])
    return f"{type(error).__name__}: {error}"


async def list_server_tools(server: ServerConfig, timeout_seconds: float) -> list[ToolInfo]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def run_session() -> list[ToolInfo]:
        async with streamable_http_client(server.url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_response = await session.list_tools()
                return [normalize_tool(server, tool) for tool in tools_response.tools]

    return await asyncio.wait_for(run_session(), timeout=timeout_seconds)


async def safe_list_server_tools(server: ServerConfig, timeout_seconds: float) -> tuple[ServerStatus, list[ToolInfo]]:
    try:
        tools = await list_server_tools(server, timeout_seconds)
        return ServerStatus(server.server_id, server.url, True, len(tools)), tools
    except (Exception, BaseExceptionGroup) as error:
        return ServerStatus(server.server_id, server.url, False, 0, compact_error(error)), []


async def collect_catalog(servers: list[ServerConfig], timeout_seconds: float) -> tuple[list[ServerStatus], list[ToolInfo]]:
    results = await asyncio.gather(*(safe_list_server_tools(server, timeout_seconds) for server in servers))
    statuses = [item[0] for item in results]
    catalog = [tool for item in results for tool in item[1]]
    return statuses, catalog


async def call_tool(server: ServerConfig, tool_name: str, arguments: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def run_session() -> dict[str, Any]:
        async with streamable_http_client(server.url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                return extract_json_from_call_result(result)

    return await asyncio.wait_for(run_session(), timeout=timeout_seconds)


def parse_repo(value: str) -> tuple[str, str]:
    parts = value.strip().split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("repo must use owner/repo format")
    return parts[0], parts[1]


def repo_from_request(request: str, default_repo: str) -> str:
    match = re.search(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b", request)
    return match.group(1) if match else default_repo


def fallback_route(repo: str, force_refresh: bool) -> list[RouteStep]:
    owner, repo_name = parse_repo(repo)
    return [
        RouteStep(
            "github",
            "get_github_repo_summary",
            {"owner": owner, "repo": repo_name},
            "Need repository metadata from GitHub MCP server.",
        ),
        RouteStep(
            "worldcup_scheduler",
            "get_worldcup_match_summary",
            {"include_matches": True, "force_refresh": force_refresh},
            "Need current scheduled World Cup summary from scheduler MCP server.",
        ),
        RouteStep(
            "worldcup_pipeline",
            "run_worldcup_pipeline",
            {"force_refresh": force_refresh, "limit": 50, "style": "short_ru", "filename_prefix": "day20_worldcup_pipeline"},
            "Need composed World Cup pipeline and saved result from pipeline MCP server.",
        ),
    ]


def normalize_route_arguments(route: list[RouteStep], repo: str, force_refresh: bool) -> list[RouteStep]:
    owner, repo_name = parse_repo(repo)
    normalized = []
    for step in route:
        args = dict(step.arguments)
        if step.server_id == "github" and step.tool_name == "get_github_repo_summary":
            args = {"owner": owner, "repo": repo_name}
        elif step.server_id == "worldcup_scheduler" and step.tool_name == "get_worldcup_match_summary":
            args = {"include_matches": True, "force_refresh": force_refresh}
        elif step.server_id == "worldcup_pipeline" and step.tool_name == "run_worldcup_pipeline":
            args = {
                "force_refresh": force_refresh,
                "limit": int(args.get("limit") or 50),
                "style": str(args.get("style") or "short_ru"),
                "filename_prefix": str(args.get("filename_prefix") or "day20_worldcup_pipeline"),
            }
            if args["limit"] < 1 or args["limit"] > 100:
                args["limit"] = 50
        normalized.append(RouteStep(step.server_id, step.tool_name, args, step.reason))
    return normalized


def route_to_json(route: list[RouteStep]) -> list[dict[str, Any]]:
    return [asdict(step) for step in route]


def route_from_json(payload: dict[str, Any]) -> list[RouteStep]:
    steps = payload.get("steps") or []
    route = []
    for item in steps:
        if not isinstance(item, dict):
            continue
        route.append(
            RouteStep(
                server_id=str(item.get("server_id") or ""),
                tool_name=str(item.get("tool_name") or ""),
                arguments=item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
                reason=str(item.get("reason") or "Selected by planner."),
            )
        )
    return route


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(stripped[start : end + 1])


def deepseek_client_from_env() -> tuple[OpenAI, str, int]:
    load_dotenv()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    timeout = parse_float(os.getenv("MEMORY_DEEPSEEK_TIMEOUT_SECONDS"), 30.0)
    max_attempts = parse_int(os.getenv("MEMORY_DEEPSEEK_RETRIES"), 2)
    client = OpenAI(api_key=require_env("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL, timeout=timeout, max_retries=0)
    return client, model, max_attempts


def ask_deepseek_json(messages: list[dict[str, str]], max_tokens: int) -> tuple[str, dict[str, int]]:
    client, model, max_attempts = deepseek_client_from_env()
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                extra_body=THINKING_DISABLED,
                temperature=0,
                max_tokens=max_tokens,
                messages=messages,
            )
            usage = response.usage
            return (
                response.choices[0].message.content.strip() if response.choices else "",
                {
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
                    "completion_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
                    "total_tokens": getattr(usage, "total_tokens", 0) if usage else 0,
                    "attempts": attempt,
                },
            )
        except APIStatusError as error:
            last_error = str(error)
            if (error.status_code not in {408, 429} and error.status_code < 500) or attempt == max_attempts:
                break
        except (APITimeoutError, APIConnectionError, APIError) as error:
            last_error = str(error)
            if attempt == max_attempts:
                break
        time.sleep(min(2 * attempt, 6))
    raise RuntimeError(last_error or "DeepSeek request failed")


def plan_route_with_deepseek(request: str, repo: str, force_refresh: bool, catalog: list[ToolInfo]) -> tuple[list[RouteStep], str]:
    catalog_payload = [
        {
            "server_id": tool.server_id,
            "tool_name": tool.tool_name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }
        for tool in catalog
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "You are an MCP tool router. Return only valid JSON. "
                "For this course task you must use exactly three steps in this order: "
                "github.get_github_repo_summary, "
                "worldcup_scheduler.get_worldcup_match_summary, "
                "worldcup_pipeline.run_worldcup_pipeline. "
                "Use only server_id and tool_name values from the catalog."
            ),
        },
        {
            "role": "user",
            "content": (
                f"User request: {request}\n"
                f"Default repo: {repo}\n"
                f"force_refresh: {force_refresh}\n"
                f"Tool catalog:\n{to_json(catalog_payload)}\n\n"
                "Return JSON shape: "
                '{"steps":[{"server_id":"...","tool_name":"...","arguments":{},"reason":"..."}]}'
            ),
        },
    ]
    text, _usage = ask_deepseek_json(messages, max_tokens=900)
    return route_from_json(parse_json_object(text)), "deepseek"


def select_route(request: str, repo: str, force_refresh: bool, catalog: list[ToolInfo]) -> tuple[list[RouteStep], str, str]:
    load_dotenv()
    planner = os.getenv("DAY20_PLANNER", "deepseek").lower()
    if planner == "deepseek" and os.getenv("DEEPSEEK_API_KEY"):
        try:
            route, source = plan_route_with_deepseek(request, repo, force_refresh, catalog)
            route = normalize_route_arguments(route, repo, force_refresh)
            route_ok, order_ok, _errors = validate_route(route, catalog)
            if route_ok and order_ok:
                return route, source, ""
            return fallback_route(repo, force_refresh), "fallback", "planner route failed validation"
        except Exception as error:
            return fallback_route(repo, force_refresh), "fallback", compact_error(error)
    return fallback_route(repo, force_refresh), "fallback", "DeepSeek planner disabled or missing key"


def validate_route(route: list[RouteStep], catalog: list[ToolInfo]) -> tuple[bool, bool, list[str]]:
    available = {(tool.server_id, tool.tool_name) for tool in catalog}
    errors = []
    for step in route:
        if (step.server_id, step.tool_name) not in available:
            errors.append(f"missing tool: {step.server_id}.{step.tool_name}")
    expected_positions = []
    for expected in DEFAULT_ROUTE:
        try:
            expected_positions.append(next(index for index, step in enumerate(route) if (step.server_id, step.tool_name) == expected))
        except StopIteration:
            errors.append(f"missing required step: {expected[0]}.{expected[1]}")
    order_ok = len(expected_positions) == len(DEFAULT_ROUTE) and expected_positions == sorted(expected_positions)
    if not order_ok:
        errors.append("required tools are not in expected order")
    return not errors, order_ok, errors


async def execute_route(
    servers: list[ServerConfig],
    route: list[RouteStep],
    timeout_seconds: float,
) -> list[ToolCallTrace]:
    server_by_id = {server.server_id: server for server in servers}
    traces = []
    for index, step in enumerate(route, start=1):
        started = time.perf_counter()
        server = server_by_id[step.server_id]
        try:
            result = await call_tool(server, step.tool_name, step.arguments, timeout_seconds)
            traces.append(
                ToolCallTrace(
                    index,
                    step.server_id,
                    server.url,
                    step.tool_name,
                    step.arguments,
                    True,
                    time.perf_counter() - started,
                    result,
                )
            )
        except (Exception, BaseExceptionGroup) as error:
            traces.append(
                ToolCallTrace(
                    index,
                    step.server_id,
                    server.url,
                    step.tool_name,
                    step.arguments,
                    False,
                    time.perf_counter() - started,
                    {},
                    compact_error(error),
                )
            )
            break
    return traces


def saved_pipeline_path(traces: list[ToolCallTrace], key: str) -> str:
    for trace in traces:
        if trace.server_id == "worldcup_pipeline":
            saved = trace.result.get("saved") or {}
            return str(saved.get(key) or "")
    return ""


def build_trace_payload(
    statuses: list[ServerStatus],
    catalog: list[ToolInfo],
    route: list[RouteStep],
    route_source: str,
    route_error: str,
    traces: list[ToolCallTrace],
    agent: AgentResult | None = None,
) -> dict[str, Any]:
    route_ok, order_ok, errors = validate_route(route, catalog)
    return {
        "generated_at": now_iso(),
        "servers": [asdict(status) for status in statuses],
        "catalog": [asdict(tool) for tool in catalog],
        "route_source": route_source,
        "route_error": route_error,
        "route_ok": route_ok,
        "order_ok": order_ok,
        "validation_errors": errors,
        "route": route_to_json(route),
        "steps": [asdict(trace) for trace in traces],
        "all_steps_ok": all(trace.ok for trace in traces) and len(traces) == len(route),
        "saved_json": saved_pipeline_path(traces, "saved_json"),
        "saved_markdown": saved_pipeline_path(traces, "saved_markdown"),
        "agent": asdict(agent) if agent else None,
    }


def save_trace(payload: dict[str, Any]) -> Path:
    root = store_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "last_flow.json"
    path.write_text(to_json(payload), encoding="utf-8")
    return path


def ask_deepseek_final(trace_payload: dict[str, Any]) -> AgentResult:
    messages = [
        {
            "role": "system",
            "content": (
                "Ты агент-оркестратор MCP. Ответь по-русски. "
                "Коротко объясни: какие MCP-серверы использованы, почему выбран такой порядок, "
                "что вернул каждый tool, где сохранен результат pipeline."
            ),
        },
        {"role": "user", "content": f"Trace JSON:\n{to_json(trace_payload)}"},
    ]
    started = time.perf_counter()
    try:
        answer, usage = ask_deepseek_json(messages, max_tokens=900)
        return AgentResult(
            answer=answer,
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            total_tokens=usage["total_tokens"],
            elapsed_seconds=time.perf_counter() - started,
            attempts=usage["attempts"],
        )
    except Exception as error:
        return AgentResult("", 0, 0, 0, time.perf_counter() - started, 0, compact_error(error))


def build_report(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Day 20. Orchestration MCP",
            "",
            "## Summary",
            "",
            "- Agent registers multiple remote MCP servers.",
            "- Agent builds one tool catalog and routes a long flow across servers.",
            "- Flow uses GitHub MCP, World Cup scheduler MCP, and World Cup pipeline MCP.",
            "",
            "## Status",
            "",
            f"- route_source: `{payload.get('route_source')}`",
            f"- route_ok: `{payload.get('route_ok')}`",
            f"- order_ok: `{payload.get('order_ok')}`",
            f"- all_steps_ok: `{payload.get('all_steps_ok')}`",
            f"- saved_json: `{payload.get('saved_json')}`",
            f"- saved_markdown: `{payload.get('saved_markdown')}`",
            "",
            "## Servers",
            "",
            "```json",
            to_json(payload.get("servers")),
            "```",
            "",
            "## Route",
            "",
            "```json",
            to_json(payload.get("route")),
            "```",
            "",
            "## Step Results",
            "",
            "```json",
            to_json(payload.get("steps")),
            "```",
            "",
            "## DeepSeek Answer",
            "",
            "```json",
            to_json(payload.get("agent")),
            "```",
            "",
            "## Check Commands",
            "",
            "```powershell",
            ".\\.venv\\Scripts\\python.exe day20_mcp_orchestrator.py servers",
            ".\\.venv\\Scripts\\python.exe day20_mcp_orchestrator.py tools",
            ".\\.venv\\Scripts\\python.exe day20_mcp_orchestrator.py route \"Сделай отчет по python/cpython и текущим матчам ЧМ, сохрани футбольную сводку\"",
            ".\\.venv\\Scripts\\python.exe day20_mcp_orchestrator.py flow --repo python/cpython --force-refresh",
            ".\\.venv\\Scripts\\python.exe day20_mcp_orchestrator.py ask \"Собери длинный отчет: GitHub repo python/cpython, текущая сводка ЧМ, и сохрани футбольный pipeline\"",
            "```",
        ]
    )


def write_report(path: str, payload: dict[str, Any]) -> Path:
    report_path = Path(path)
    report_path.write_text(build_report(payload), encoding="utf-8")
    return report_path.resolve()


def print_servers(statuses: list[ServerStatus]) -> None:
    print(f"servers_count={len(statuses)}")
    print(f"connected_servers={sum(1 for status in statuses if status.connected)}")
    for status in statuses:
        line = f"server={status.server_id} connected={status.connected} tools_count={status.tools_count} url={status.server_url}"
        if status.error:
            line += f" error={status.error}"
        print(line)


def print_tools(catalog: list[ToolInfo]) -> None:
    print(f"tools_count={len(catalog)}")
    for tool in catalog:
        print(f"tool={tool.server_id}.{tool.tool_name}")
        print(f"description={tool.description}")
        required = tool.input_schema.get("required") or []
        print(f"required={required}")


def print_route(route: list[RouteStep], route_source: str, route_error: str, catalog: list[ToolInfo]) -> None:
    route_ok, order_ok, errors = validate_route(route, catalog)
    print(f"route_source={route_source}")
    if route_error:
        print(f"route_error={route_error}")
    print(f"route_ok={route_ok}")
    print(f"order_ok={order_ok}")
    if errors:
        print(f"errors={errors}")
    for index, step in enumerate(route, start=1):
        print(f"step_{index} server={step.server_id} tool={step.tool_name} args={to_json(step.arguments)}")
        print(f"reason={step.reason}")


def print_flow(payload: dict[str, Any], trace_path: Path, report_path: Path | None = None) -> None:
    print(f"route_source={payload.get('route_source')}")
    if payload.get("route_error"):
        print(f"route_error={payload.get('route_error')}")
    print(f"route_ok={payload.get('route_ok')}")
    print(f"order_ok={payload.get('order_ok')}")
    print(f"all_steps_ok={payload.get('all_steps_ok')}")
    for trace in payload.get("steps") or []:
        print(
            f"step_{trace['step']} server={trace['server_id']} tool={trace['tool_name']} "
            f"ok={trace['ok']} elapsed={trace['elapsed_seconds']:.2f}"
        )
        if trace.get("error"):
            print(f"error={trace['error']}")
    if payload.get("saved_json"):
        print(f"saved_json={payload.get('saved_json')}")
    if payload.get("saved_markdown"):
        print(f"saved_markdown={payload.get('saved_markdown')}")
    print(f"trace_saved={trace_path.resolve()}")
    if report_path:
        print(f"report_saved={report_path}")
    agent = payload.get("agent") or {}
    if agent:
        print(f"deepseek_used={bool(agent.get('answer'))}")
        if agent.get("error"):
            print(f"deepseek_error={agent.get('error')}")
        if agent.get("answer"):
            print(f"tokens prompt={agent.get('prompt_tokens')} completion={agent.get('completion_tokens')} total={agent.get('total_tokens')}")
            print("agent_answer:")
            print(agent.get("answer"))


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--timeout", type=float, default=None, help="MCP timeout in seconds")


def add_flow_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", default="python/cpython", help="GitHub repo in owner/repo format")
    parser.add_argument("--force-refresh", action="store_true", help="Force live refresh for World Cup tools")
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH, help="Markdown report path")


async def command_servers(args: argparse.Namespace) -> int:
    statuses, _catalog = await collect_catalog(load_servers(), timeout_from_env(args))
    print_servers(statuses)
    return 0 if all(status.connected for status in statuses) else 1


async def command_tools(args: argparse.Namespace) -> int:
    statuses, catalog = await collect_catalog(load_servers(), timeout_from_env(args))
    print_servers(statuses)
    print_tools(catalog)
    server_ids = {tool.server_id for tool in catalog}
    return 0 if {"github", "worldcup_scheduler", "worldcup_pipeline"} <= server_ids and len(catalog) >= 6 else 1


async def command_route(args: argparse.Namespace) -> int:
    request = args.request
    repo = repo_from_request(request, args.repo)
    statuses, catalog = await collect_catalog(load_servers(), timeout_from_env(args))
    route, route_source, route_error = select_route(request, repo, args.force_refresh, catalog)
    print_servers(statuses)
    print_route(route, route_source, route_error, catalog)
    route_ok, order_ok, _errors = validate_route(route, catalog)
    return 0 if route_ok and order_ok else 1


async def run_flow(request: str, repo: str, force_refresh: bool, timeout_seconds: float) -> tuple[dict[str, Any], Path]:
    servers = load_servers()
    statuses, catalog = await collect_catalog(servers, timeout_seconds)
    route, route_source, route_error = select_route(request, repo, force_refresh, catalog)
    traces = await execute_route(servers, route, timeout_seconds)
    payload = build_trace_payload(statuses, catalog, route, route_source, route_error, traces)
    trace_path = save_trace(payload)
    return payload, trace_path


async def command_flow(args: argparse.Namespace) -> int:
    request = f"Flow for GitHub repo {args.repo}, current World Cup summary, and saved World Cup pipeline."
    payload, trace_path = await run_flow(request, args.repo, args.force_refresh, timeout_from_env(args))
    report_path = write_report(args.report, payload)
    print_flow(payload, trace_path, report_path)
    return 0 if payload.get("route_ok") and payload.get("order_ok") and payload.get("all_steps_ok") else 1


async def command_ask(args: argparse.Namespace) -> int:
    repo = repo_from_request(args.request, args.repo)
    payload, trace_path = await run_flow(args.request, repo, args.force_refresh, timeout_from_env(args))
    agent = ask_deepseek_final(payload)
    payload["agent"] = asdict(agent)
    trace_path = save_trace(payload)
    report_path = write_report(args.report, payload)
    print_flow(payload, trace_path, report_path)
    return 0 if payload.get("all_steps_ok") and not agent.error else 1


def run_or_print_error(coro: Any) -> int:
    try:
        return asyncio.run(coro)
    except (Exception, BaseExceptionGroup) as error:
        print(f"error={compact_error(error)}")
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Day 20 MCP orchestrator across multiple remote MCP servers.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    servers_parser = subparsers.add_parser("servers", help="check all registered MCP servers")
    add_common_args(servers_parser)

    tools_parser = subparsers.add_parser("tools", help="print merged MCP tool catalog")
    add_common_args(tools_parser)

    route_parser = subparsers.add_parser("route", help="plan route without executing tools")
    add_common_args(route_parser)
    route_parser.add_argument("request", help="User request to route")
    route_parser.add_argument("--repo", default="python/cpython")
    route_parser.add_argument("--force-refresh", action="store_true")

    flow_parser = subparsers.add_parser("flow", help="execute long deterministic MCP flow")
    add_common_args(flow_parser)
    add_flow_args(flow_parser)

    ask_parser = subparsers.add_parser("ask", help="route, execute flow, and ask DeepSeek for final answer")
    add_common_args(ask_parser)
    ask_parser.add_argument("request", help="User request to route and execute")
    add_flow_args(ask_parser)

    args = parser.parse_args()
    if args.command == "servers":
        raise SystemExit(run_or_print_error(command_servers(args)))
    if args.command == "tools":
        raise SystemExit(run_or_print_error(command_tools(args)))
    if args.command == "route":
        raise SystemExit(run_or_print_error(command_route(args)))
    if args.command == "flow":
        raise SystemExit(run_or_print_error(command_flow(args)))
    if args.command == "ask":
        raise SystemExit(run_or_print_error(command_ask(args)))


if __name__ == "__main__":
    main()
