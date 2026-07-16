import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8004
DEFAULT_STORE_DIR = "day19_pipeline_store"
DEFAULT_WINDOW_DAYS = 7
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_int(value: str | None, default: int) -> int:
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def parse_day(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Date must use YYYY-MM-DD or YYYYMMDD format: {value}")


def store_root() -> Path:
    return Path(os.getenv("DAY19_STORE_DIR", DEFAULT_STORE_DIR))


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def run_path(pipeline_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", pipeline_id)
    return store_root() / "runs" / f"{safe_id}.json"


def save_run(run: dict[str, Any]) -> None:
    atomic_write_json(run_path(str(run["pipeline_id"])), run)


def load_run(pipeline_id: str) -> dict[str, Any]:
    run = read_json(run_path(pipeline_id))
    if not run:
        raise ValueError(f"Pipeline run not found: {pipeline_id}")
    return run


def update_run(pipeline_id: str, step_name: str, payload: dict[str, Any]) -> None:
    run = load_run(pipeline_id)
    run.setdefault("steps", {})[step_name] = payload
    run["updated_at"] = iso_now()
    save_run(run)


def event_link(event: dict[str, Any]) -> str:
    for link in event.get("links") or []:
        rel = link.get("rel") or []
        if "summary" in rel:
            return link.get("href") or ""
    return ""


def parse_event(event: dict[str, Any]) -> dict[str, Any]:
    competition = (event.get("competitions") or [{}])[0]
    status = competition.get("status") or event.get("status") or {}
    status_type = status.get("type") or {}
    venue = competition.get("venue") or event.get("venue") or {}
    competitors = competition.get("competitors") or []
    teams = []
    for competitor in competitors:
        team = competitor.get("team") or {}
        teams.append(
            {
                "home_away": competitor.get("homeAway"),
                "name": team.get("displayName") or team.get("name"),
                "abbreviation": team.get("abbreviation"),
                "score": competitor.get("score"),
                "winner": competitor.get("winner"),
            }
        )
    teams.sort(key=lambda item: 0 if item.get("home_away") == "home" else 1)
    return {
        "id": event.get("id"),
        "name": event.get("name"),
        "short_name": event.get("shortName"),
        "date": event.get("date"),
        "stage": (event.get("season") or {}).get("slug"),
        "group": competition.get("altGameNote"),
        "status_state": status_type.get("state"),
        "status": status_type.get("description"),
        "status_detail": status_type.get("detail") or status_type.get("shortDetail"),
        "completed": status_type.get("completed"),
        "venue": venue.get("fullName") or venue.get("displayName"),
        "city": ((venue.get("address") or {}).get("city")),
        "teams": teams,
        "link": event_link(event),
    }


def score_line(match: dict[str, Any]) -> str:
    teams = match.get("teams") or []
    if len(teams) < 2:
        return match.get("name") or "unknown match"
    home, away = teams[0], teams[1]
    home_score = home.get("score") if home.get("score") not in {None, ""} else "-"
    away_score = away.get("score") if away.get("score") not in {None, ""} else "-"
    return f"{home.get('name')} {home_score} - {away_score} {away.get('name')}"


def match_search_text(match: dict[str, Any]) -> str:
    teams = " ".join(str(team.get("name") or "") for team in match.get("teams") or [])
    values = [
        match.get("name"),
        match.get("short_name"),
        match.get("group"),
        match.get("status"),
        match.get("status_detail"),
        match.get("venue"),
        match.get("city"),
        teams,
    ]
    return " ".join(str(value or "") for value in values).lower()


def aggregate_matches(matches: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [item for item in matches if item.get("status_state") == "post"]
    live = [item for item in matches if item.get("status_state") == "in"]
    scheduled = [item for item in matches if item.get("status_state") == "pre"]
    return {
        "total_matches": len(matches),
        "completed_matches": len(completed),
        "live_matches": len(live),
        "scheduled_matches": len(scheduled),
        "latest_results": [score_line(item) for item in completed[:8]],
        "live_now": [score_line(item) for item in live[:8]],
        "next_matches": [
            {
                "date": item.get("date"),
                "match": item.get("name"),
                "status": item.get("status_detail"),
                "venue": item.get("venue"),
                "group": item.get("group"),
            }
            for item in scheduled[:10]
        ],
    }


async def fetch_day(client: httpx.AsyncClient, target_day: date) -> list[dict[str, Any]]:
    response = await client.get(ESPN_SCOREBOARD_URL, params={"dates": target_day.strftime("%Y%m%d")})
    response.raise_for_status()
    data = response.json()
    return [parse_event(event) for event in data.get("events") or []]


def requested_days(date_from: str | None, date_to: str | None, window_days: int) -> list[date]:
    start = parse_day(date_from) or (utc_now().date() - timedelta(days=1))
    end = parse_day(date_to) or (start + timedelta(days=max(1, window_days) - 1))
    if end < start:
        raise ValueError("date_to must be greater than or equal to date_from")
    total_days = min((end - start).days + 1, 14)
    return [start + timedelta(days=offset) for offset in range(total_days)]


async def fetch_matches(date_from: str | None, date_to: str | None, window_days: int) -> tuple[list[dict[str, Any]], list[str]]:
    days = requested_days(date_from, date_to, window_days)
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "day19-worldcup-pipeline-mcp"}) as client:
        batches = await asyncio.gather(*(fetch_day(client, target_day) for target_day in days))
    matches = [match for batch in batches for match in batch]
    matches.sort(key=lambda item: item.get("date") or "")
    return matches, [target_day.isoformat() for target_day in days]


async def search_worldcup_matches_impl(
    *,
    date_from: str | None,
    date_to: str | None,
    query: str | None,
    force_refresh: bool,
    limit: int,
    window_days: int,
) -> dict[str, Any]:
    del force_refresh
    matches, days = await fetch_matches(date_from, date_to, window_days)
    if query:
        needle = query.lower()
        matches = [match for match in matches if needle in match_search_text(match)]
    limit = max(1, min(limit, 100))
    matches = matches[:limit]
    pipeline_id = f"{utc_now().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    result = {
        "pipeline_id": pipeline_id,
        "step": "search_worldcup_matches",
        "source": "espn_scoreboard_api",
        "source_url": ESPN_SCOREBOARD_URL,
        "fetched_at": iso_now(),
        "requested_days": days,
        "query": query or "",
        "limit": limit,
        "matches": matches,
        "aggregate": aggregate_matches(matches),
    }
    save_run(
        {
            "pipeline_id": pipeline_id,
            "created_at": result["fetched_at"],
            "updated_at": result["fetched_at"],
            "steps": {"search_worldcup_matches": result},
        }
    )
    return result


def summary_text(aggregate: dict[str, Any]) -> str:
    parts = [
        f"Found {aggregate.get('total_matches', 0)} World Cup matches.",
        f"Completed: {aggregate.get('completed_matches', 0)}.",
        f"Live: {aggregate.get('live_matches', 0)}.",
        f"Scheduled: {aggregate.get('scheduled_matches', 0)}.",
    ]
    latest = aggregate.get("latest_results") or []
    upcoming = aggregate.get("next_matches") or []
    if latest:
        parts.append("Latest results: " + "; ".join(latest[:4]) + ".")
    if upcoming:
        parts.append(
            "Next matches: "
            + "; ".join(f"{item.get('match')} ({item.get('status')})" for item in upcoming[:4])
            + "."
        )
    return " ".join(parts)


def summarize_worldcup_matches_impl(
    *,
    pipeline_id: str | None,
    matches: list[dict[str, Any]] | None,
    style: str,
) -> dict[str, Any]:
    source_pipeline_id = pipeline_id or ""
    if matches is None:
        if not pipeline_id:
            raise ValueError("Either pipeline_id or matches must be provided")
        run = load_run(pipeline_id)
        search_result = run.get("steps", {}).get("search_worldcup_matches")
        if not search_result:
            raise ValueError(f"Search step is missing for pipeline_id={pipeline_id}")
        matches = search_result.get("matches") or []
    aggregate = aggregate_matches(matches)
    result = {
        "pipeline_id": source_pipeline_id,
        "source_pipeline_id": source_pipeline_id,
        "step": "summarize_worldcup_matches",
        "style": style,
        "generated_at": iso_now(),
        "summary_text": summary_text(aggregate),
        "aggregate": aggregate,
        "key_results": aggregate.get("latest_results", []),
        "live_now": aggregate.get("live_now", []),
        "upcoming": aggregate.get("next_matches", []),
    }
    if pipeline_id:
        update_run(pipeline_id, "summarize_worldcup_matches", result)
    return result


def summary_markdown(summary: dict[str, Any]) -> str:
    aggregate = summary.get("aggregate") or {}
    lines = [
        "# World Cup Pipeline Summary",
        "",
        f"- pipeline_id: `{summary.get('pipeline_id')}`",
        f"- generated_at: `{summary.get('generated_at')}`",
        f"- total_matches: `{aggregate.get('total_matches')}`",
        f"- completed_matches: `{aggregate.get('completed_matches')}`",
        f"- live_matches: `{aggregate.get('live_matches')}`",
        f"- scheduled_matches: `{aggregate.get('scheduled_matches')}`",
        "",
        "## Summary",
        "",
        summary.get("summary_text") or "",
        "",
        "## Latest Results",
        "",
    ]
    latest = summary.get("key_results") or []
    lines.extend([f"- {item}" for item in latest] or ["- none"])
    lines.extend(["", "## Upcoming", ""])
    upcoming = summary.get("upcoming") or []
    lines.extend([f"- {item.get('date')} | {item.get('match')} | {item.get('status')}" for item in upcoming] or ["- none"])
    lines.append("")
    return "\n".join(lines)


def save_worldcup_summary_impl(
    *,
    pipeline_id: str,
    summary: dict[str, Any] | None,
    filename_prefix: str,
) -> dict[str, Any]:
    if summary is None:
        run = load_run(pipeline_id)
        summary = run.get("steps", {}).get("summarize_worldcup_matches")
        if not summary:
            raise ValueError(f"Summary step is missing for pipeline_id={pipeline_id}")
    safe_prefix = re.sub(r"[^A-Za-z0-9_.-]", "_", filename_prefix or "worldcup_pipeline")
    base_name = f"{safe_prefix}_{pipeline_id}"
    summary_dir = store_root() / "summaries"
    json_path = summary_dir / f"{base_name}.json"
    md_path = summary_dir / f"{base_name}.md"
    atomic_write_json(json_path, summary)
    summary_dir.mkdir(parents=True, exist_ok=True)
    md_path.write_text(summary_markdown(summary), encoding="utf-8")
    result = {
        "pipeline_id": pipeline_id,
        "step": "save_worldcup_summary",
        "saved_at": iso_now(),
        "saved_json": str(json_path.resolve()),
        "saved_markdown": str(md_path.resolve()),
        "summary_bytes": json_path.stat().st_size,
        "markdown_bytes": md_path.stat().st_size,
    }
    update_run(pipeline_id, "save_worldcup_summary", result)
    return result


async def run_worldcup_pipeline_impl(
    *,
    date_from: str | None,
    date_to: str | None,
    query: str | None,
    force_refresh: bool,
    limit: int,
    style: str,
    filename_prefix: str,
    window_days: int,
) -> dict[str, Any]:
    search = await search_worldcup_matches_impl(
        date_from=date_from,
        date_to=date_to,
        query=query,
        force_refresh=force_refresh,
        limit=limit,
        window_days=window_days,
    )
    pipeline_id = str(search["pipeline_id"])
    summary = summarize_worldcup_matches_impl(pipeline_id=pipeline_id, matches=None, style=style)
    saved = save_worldcup_summary_impl(pipeline_id=pipeline_id, summary=summary, filename_prefix=filename_prefix)
    return {
        "pipeline_id": pipeline_id,
        "step": "run_worldcup_pipeline",
        "source_url": ESPN_SCOREBOARD_URL,
        "automatic_chain": ["search_worldcup_matches", "summarize_worldcup_matches", "save_worldcup_summary"],
        "search": search,
        "summary": summary,
        "saved": saved,
    }


def create_mcp_server(host: str, port: int, window_days: int) -> FastMCP:
    mcp = FastMCP(
        name="day19-worldcup-pipeline-mcp",
        instructions="Remote MCP server that composes search, summarize, and save tools for real FIFA World Cup match data.",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool(
        name="search_worldcup_matches",
        description="Fetch and search real FIFA World Cup matches from the ESPN scoreboard API.",
        structured_output=True,
    )
    async def search_worldcup_matches(
        date_from: Annotated[
            str | None,
            Field(description="Optional start date in YYYY-MM-DD format. Defaults to yesterday UTC."),
        ] = None,
        date_to: Annotated[
            str | None,
            Field(description="Optional end date in YYYY-MM-DD format. Defaults to date_from plus the server window."),
        ] = None,
        query: Annotated[
            str | None,
            Field(description="Optional text filter for team, venue, group, or match name."),
        ] = None,
        force_refresh: Annotated[
            bool,
            Field(description="Kept for pipeline symmetry. Data is fetched live on every search call."),
        ] = False,
        limit: Annotated[
            int,
            Field(description="Maximum number of matches to return.", ge=1, le=100),
        ] = 50,
    ) -> dict[str, Any]:
        return await search_worldcup_matches_impl(
            date_from=date_from,
            date_to=date_to,
            query=query,
            force_refresh=force_refresh,
            limit=limit,
            window_days=window_days,
        )

    @mcp.tool(
        name="summarize_worldcup_matches",
        description="Summarize World Cup matches from a previous search pipeline_id or an explicit matches array.",
        structured_output=True,
    )
    async def summarize_worldcup_matches(
        pipeline_id: Annotated[
            str | None,
            Field(description="Pipeline id returned by search_worldcup_matches."),
        ] = None,
        matches: Annotated[
            list[dict[str, Any]] | None,
            Field(description="Optional explicit matches array. If omitted, pipeline_id is used."),
        ] = None,
        style: Annotated[
            str,
            Field(description="Summary style label, for example short_ru or report."),
        ] = "short_ru",
    ) -> dict[str, Any]:
        return summarize_worldcup_matches_impl(pipeline_id=pipeline_id, matches=matches, style=style)

    @mcp.tool(
        name="save_worldcup_summary",
        description="Save a World Cup summary from a previous pipeline step to JSON and Markdown files.",
        structured_output=True,
    )
    async def save_worldcup_summary(
        pipeline_id: Annotated[
            str,
            Field(description="Pipeline id returned by search_worldcup_matches."),
        ],
        summary: Annotated[
            dict[str, Any] | None,
            Field(description="Optional summary payload. If omitted, the stored summary step is used."),
        ] = None,
        filename_prefix: Annotated[
            str,
            Field(description="Safe filename prefix for saved JSON and Markdown files."),
        ] = "worldcup_pipeline",
    ) -> dict[str, Any]:
        return save_worldcup_summary_impl(pipeline_id=pipeline_id, summary=summary, filename_prefix=filename_prefix)

    @mcp.tool(
        name="run_worldcup_pipeline",
        description="Automatically run search_worldcup_matches -> summarize_worldcup_matches -> save_worldcup_summary.",
        structured_output=True,
    )
    async def run_worldcup_pipeline(
        date_from: Annotated[
            str | None,
            Field(description="Optional start date in YYYY-MM-DD format. Defaults to yesterday UTC."),
        ] = None,
        date_to: Annotated[
            str | None,
            Field(description="Optional end date in YYYY-MM-DD format. Defaults to date_from plus the server window."),
        ] = None,
        query: Annotated[
            str | None,
            Field(description="Optional text filter for team, venue, group, or match name."),
        ] = None,
        force_refresh: Annotated[
            bool,
            Field(description="Kept for pipeline symmetry. Data is fetched live on every search call."),
        ] = False,
        limit: Annotated[
            int,
            Field(description="Maximum number of matches to return.", ge=1, le=100),
        ] = 50,
        style: Annotated[
            str,
            Field(description="Summary style label, for example short_ru or report."),
        ] = "short_ru",
        filename_prefix: Annotated[
            str,
            Field(description="Safe filename prefix for saved JSON and Markdown files."),
        ] = "worldcup_pipeline",
    ) -> dict[str, Any]:
        return await run_worldcup_pipeline_impl(
            date_from=date_from,
            date_to=date_to,
            query=query,
            force_refresh=force_refresh,
            limit=limit,
            style=style,
            filename_prefix=filename_prefix,
            window_days=window_days,
        )

    return mcp


def print_schema(host: str, port: int, window_days: int) -> None:
    server = create_mcp_server(host, port, window_days)
    tools = server._tool_manager.list_tools()
    payload = [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters,
            "output_schema": tool.output_schema,
        }
        for tool in tools
    ]
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Day 19 composed MCP tools for World Cup match pipelines.")
    parser.add_argument("--window-days", type=int, default=parse_int(os.getenv("DAY19_WINDOW_DAYS"), DEFAULT_WINDOW_DAYS))
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="run Streamable HTTP MCP server")
    serve_parser.add_argument("--host", default=DEFAULT_HOST)
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT)

    schema_parser = subparsers.add_parser("schema", help="print registered tool schema")
    schema_parser.add_argument("--host", default=DEFAULT_HOST)
    schema_parser.add_argument("--port", type=int, default=DEFAULT_PORT)

    run_parser = subparsers.add_parser("run-once", help="run the pipeline without MCP transport")
    run_parser.add_argument("--date-from", default=None)
    run_parser.add_argument("--date-to", default=None)
    run_parser.add_argument("--query", default=None)
    run_parser.add_argument("--force-refresh", action="store_true")
    run_parser.add_argument("--limit", type=int, default=50)

    args = parser.parse_args()
    if args.command == "serve":
        server = create_mcp_server(args.host, args.port, args.window_days)
        print(f"starting day19-worldcup-pipeline-mcp on {args.host}:{args.port}/mcp", flush=True)
        asyncio.run(server.run_streamable_http_async())
    elif args.command == "schema":
        print_schema(args.host, args.port, args.window_days)
    elif args.command == "run-once":
        result = asyncio.run(
            run_worldcup_pipeline_impl(
                date_from=args.date_from,
                date_to=args.date_to,
                query=args.query,
                force_refresh=args.force_refresh,
                limit=args.limit,
                style="short_ru",
                filename_prefix="worldcup_pipeline",
                window_days=args.window_days,
            )
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
