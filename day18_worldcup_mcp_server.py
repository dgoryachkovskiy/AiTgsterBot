import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8002
DEFAULT_REFRESH_SECONDS = 7200
DEFAULT_STORE_DIR = "day18_worldcup_store"
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


def store_path() -> Path:
    root = Path(os.getenv("DAY18_STORE_DIR", DEFAULT_STORE_DIR))
    return root / "worldcup_summary.json"


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


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
    teams.sort(key=lambda item: 0 if item["home_away"] == "home" else 1)
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
    return f"{home.get('name')} {home.get('score')} - {away.get('score')} {away.get('name')}"


def aggregate_matches(matches: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [item for item in matches if item.get("status_state") == "post"]
    live = [item for item in matches if item.get("status_state") == "in"]
    scheduled = [item for item in matches if item.get("status_state") == "pre"]
    return {
        "total_matches": len(matches),
        "completed_matches": len(completed),
        "live_matches": len(live),
        "scheduled_matches": len(scheduled),
        "latest_results": [score_line(item) for item in completed[:6]],
        "live_now": [score_line(item) for item in live[:6]],
        "next_matches": [
            {
                "date": item.get("date"),
                "match": item.get("name"),
                "status": item.get("status_detail"),
                "venue": item.get("venue"),
                "group": item.get("group"),
            }
            for item in scheduled[:8]
        ],
    }


async def fetch_day(client: httpx.AsyncClient, day: datetime) -> list[dict[str, Any]]:
    params = {"dates": day.strftime("%Y%m%d")}
    response = await client.get(ESPN_SCOREBOARD_URL, params=params)
    response.raise_for_status()
    data = response.json()
    return [parse_event(event) for event in data.get("events") or []]


async def collect_worldcup_snapshot(window_days: int) -> dict[str, Any]:
    window_days = max(1, min(window_days, 7))
    start_day = utc_now().date() - timedelta(days=1)
    days = [datetime.combine(start_day + timedelta(days=offset), datetime.min.time(), tzinfo=timezone.utc) for offset in range(window_days)]
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "day18-worldcup-mcp"}) as client:
        batches = await asyncio.gather(*(fetch_day(client, day) for day in days))
    matches = [match for batch in batches for match in batch]
    matches.sort(key=lambda item: item.get("date") or "")
    snapshot = {
        "source": "espn_scoreboard_api",
        "source_url": ESPN_SCOREBOARD_URL,
        "fetched_at": iso_now(),
        "window_days": window_days,
        "matches": matches,
        "aggregate": aggregate_matches(matches),
    }
    atomic_write_json(store_path(), snapshot)
    return snapshot


async def scheduler_loop(refresh_seconds: int, window_days: int) -> None:
    while True:
        try:
            snapshot = await collect_worldcup_snapshot(window_days)
            print(
                f"worldcup_snapshot_saved fetched_at={snapshot['fetched_at']} matches={snapshot['aggregate']['total_matches']}",
                flush=True,
            )
        except Exception as error:
            print(f"worldcup_snapshot_error={type(error).__name__}: {error}", flush=True)
        await asyncio.sleep(refresh_seconds)


def get_stored_snapshot() -> dict[str, Any] | None:
    snapshot = read_json(store_path())
    if snapshot:
        return snapshot
    return None


def create_mcp_server(host: str, port: int, refresh_seconds: int, window_days: int) -> FastMCP:
    mcp = FastMCP(
        name="day18-worldcup-summary-mcp",
        instructions="Remote MCP server that runs a background scheduler and stores FIFA World Cup match summaries.",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool(
        name="get_worldcup_match_summary",
        description=(
            "Return the latest aggregated FIFA World Cup match summary collected by the server background scheduler. "
            "The server stores snapshots in JSON and refreshes them periodically."
        ),
        structured_output=True,
    )
    async def get_worldcup_match_summary(
        include_matches: Annotated[
            bool,
            Field(description="When true, include normalized match list from stored scheduler snapshot."),
        ] = True,
        force_refresh: Annotated[
            bool,
            Field(description="When true, fetch ESPN data now before returning the aggregated summary."),
        ] = False,
    ) -> dict[str, Any]:
        snapshot = None
        if force_refresh:
            snapshot = await collect_worldcup_snapshot(window_days)
        if snapshot is None:
            snapshot = get_stored_snapshot()
        if snapshot is None:
            snapshot = await collect_worldcup_snapshot(window_days)
        result = {
            "scheduler": {
                "enabled": True,
                "refresh_seconds": refresh_seconds,
                "store_path": str(store_path()),
                "last_fetched_at": snapshot.get("fetched_at"),
                "force_refresh_used": force_refresh,
            },
            "source": snapshot.get("source"),
            "source_url": snapshot.get("source_url"),
            "aggregate": snapshot.get("aggregate"),
        }
        if include_matches:
            result["matches"] = snapshot.get("matches", [])
        return result

    return mcp


async def run_server_with_scheduler(server: FastMCP, refresh_seconds: int, window_days: int) -> None:
    scheduler_task = asyncio.create_task(scheduler_loop(refresh_seconds, window_days))
    try:
        await server.run_streamable_http_async()
    finally:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass


def print_schema(host: str, port: int, refresh_seconds: int, window_days: int) -> None:
    server = create_mcp_server(host, port, refresh_seconds, window_days)
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
    parser = argparse.ArgumentParser(description="Day 18 World Cup scheduled summary MCP server.")
    parser.add_argument("--refresh-seconds", type=int, default=parse_int(os.getenv("DAY18_REFRESH_SECONDS"), DEFAULT_REFRESH_SECONDS))
    parser.add_argument("--window-days", type=int, default=parse_int(os.getenv("DAY18_WINDOW_DAYS"), 3))
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="run Streamable HTTP MCP server with background scheduler")
    serve_parser.add_argument("--host", default=DEFAULT_HOST)
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT)

    schema_parser = subparsers.add_parser("schema", help="print registered tool schema")
    schema_parser.add_argument("--host", default=DEFAULT_HOST)
    schema_parser.add_argument("--port", type=int, default=DEFAULT_PORT)

    fetch_parser = subparsers.add_parser("fetch-once", help="fetch and store one World Cup snapshot")
    fetch_parser.add_argument("--window-days", type=int, default=parse_int(os.getenv("DAY18_WINDOW_DAYS"), 3))

    args = parser.parse_args()
    if args.command == "serve":
        server = create_mcp_server(args.host, args.port, args.refresh_seconds, args.window_days)
        print(
            f"starting day18-worldcup-summary-mcp on {args.host}:{args.port}/mcp refresh={args.refresh_seconds}s",
            flush=True,
        )
        asyncio.run(run_server_with_scheduler(server, args.refresh_seconds, args.window_days))
    elif args.command == "schema":
        print_schema(args.host, args.port, args.refresh_seconds, args.window_days)
    elif args.command == "fetch-once":
        snapshot = asyncio.run(collect_worldcup_snapshot(args.window_days))
        print(json.dumps({"saved": str(store_path()), "aggregate": snapshot["aggregate"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
