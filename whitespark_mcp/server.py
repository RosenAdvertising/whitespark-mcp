"""MCP server and read-only tool registration for Whitespark."""

from __future__ import annotations

import asyncio
import base64
import math
import os
import re
from collections.abc import Mapping
from contextlib import asynccontextmanager
from numbers import Real
from typing import Annotated, Any, Literal
from uuid import UUID

from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.applications import Starlette

from whitespark_mcp import __version__
from whitespark_mcp.client import WhitesparkAPIError, WhitesparkClient
from whitespark_mcp.config import WhitesparkConfig
from whitespark_mcp.paths import path_id
from whitespark_mcp.runtime import LazyClient
from whitespark_mcp.runtime import SafeMCPServer as MCPServer

PROTOCOL_VERSION = "2026-07-28"
SERVER_NAME = "whitespark-mcp"
MAX_BATCH_SIZE = 25
STREAMABLE_HTTP_TRANSPORT = "streamable-http"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

Page = Annotated[int, Field(description="Whitespark page number, starting at 1", ge=1)]
PerPage = Annotated[
    int,
    Field(description="Requested page size; Whitespark caps values above 100 at 100", ge=1),
]
ResultLimit = Annotated[
    int,
    Field(description="Maximum number of grid summaries to return", ge=1, le=10_000),
]
GridId = Annotated[UUID, Field(description="Grid UUID shown in the Whitespark grid URL")]
GridIds = Annotated[
    list[UUID],
    Field(
        description="Grid UUIDs shown in Whitespark grid URLs",
        min_length=1,
        max_length=MAX_BATCH_SIZE,
    ),
]
SortDirection = Annotated[
    Literal["ascending", "descending"],
    Field(description="Whether smaller or larger metric values appear first"),
]
ScreenshotMode = Annotated[
    Literal["with-metrics", "without-metrics"],
    Field(description="Include metric annotations or return the clean grid image"),
]


def create_server(
    *,
    config: WhitesparkConfig | None = None,
    client: WhitesparkClient | LazyClient | None = None,
) -> MCPServer:
    """Create a configured MCP server, validating environment settings when needed."""
    if config is not None and client is not None:
        raise ValueError("Pass config or client, not both.")
    api_client = client or (WhitesparkClient(config) if config else LazyClient())

    @asynccontextmanager
    async def lifespan(_server: MCPServer):
        try:
            yield {"whitespark_client": api_client}
        finally:
            await api_client.aclose()

    server = MCPServer(
        name=SERVER_NAME,
        title="Whitespark Local Ranking Grids",
        description="Read documented Whitespark Local Ranking Grid summaries and screenshots.",
        instructions=(
            "This server is read-only. Tools prefixed experimental_ depend on response field "
            "names that Whitespark's public beta guide does not specify."
        ),
        version=__version__,
        lifespan=lifespan,
    )
    register_tools(server, api_client)
    return server


def register_tools(server: MCPServer, api_client: WhitesparkClient) -> None:
    """Register all documented read tools on an MCP server."""

    @server.tool(structured_output=True)
    async def list_ranking_grids(
        page: Page = 1,
        per_page: PerPage = 50,
        limit: ResultLimit = 50,
    ) -> dict[str, Any]:
        """Show one page of ranking grids configured in your Whitespark account."""
        result = await api_client.list_grids_page(page=page, per_page=per_page)
        data = result["data"][:limit]
        return {
            "data": data,
            "meta": {
                **result["meta"],
                "returned": len(data),
                "requested_limit": limit,
            },
        }

    @server.tool(structured_output=True)
    async def list_all_ranking_grids(
        limit: ResultLimit = 500,
        per_page: PerPage = 100,
    ) -> dict[str, Any]:
        """Show ranking grids across pages, up to the limit you choose."""
        return await api_client.list_all_grids(limit=limit, per_page=per_page)

    @server.tool(structured_output=True)
    async def experimental_get_ranking_grid_summary(
        grid_id: GridId,
        page: Page = 1,
        per_page: PerPage = 100,
    ) -> dict[str, Any]:
        """Find one grid on a chosen page using a best-effort identifier-field match."""
        result = await api_client.list_grids_page(page=page, per_page=per_page)
        summary = _find_summary(result["data"], str(grid_id))
        if summary is None:
            raise ValueError(f"Grid {grid_id} was not found on Whitespark page {page}.")
        return {"grid_id": str(grid_id), "summary": summary, "page_meta": result["meta"]}

    @server.tool(structured_output=True)
    async def experimental_batch_get_ranking_grid_summaries(
        grid_ids: GridIds,
        page: Page = 1,
        per_page: PerPage = 100,
    ) -> dict[str, Any]:
        """Find several grids on a chosen page using best-effort identifier-field matches."""
        result = await api_client.list_grids_page(page=page, per_page=per_page)
        requested = [str(grid_id) for grid_id in grid_ids]
        matches = _match_summaries(result["data"], requested)
        return {
            "data": [matches[grid_id] for grid_id in requested if grid_id in matches],
            "not_found": [grid_id for grid_id in requested if grid_id not in matches],
            "page_meta": result["meta"],
        }

    @server.tool(structured_output=True)
    async def experimental_compare_grid_summaries(
        grid_id_a: GridId,
        grid_id_b: GridId,
        page: Page = 1,
        per_page: PerPage = 100,
    ) -> dict[str, Any]:
        """Compare the current documented metrics for two grids on a chosen page."""
        result = await api_client.list_grids_page(page=page, per_page=per_page)
        summary_a = _find_summary(result["data"], str(grid_id_a))
        summary_b = _find_summary(result["data"], str(grid_id_b))
        missing = [
            grid_id
            for grid_id, summary in ((str(grid_id_a), summary_a), (str(grid_id_b), summary_b))
            if summary is None
        ]
        if missing:
            raise ValueError(f"Grid IDs not found on Whitespark page {page}: {', '.join(missing)}")
        assert summary_a is not None and summary_b is not None
        differences = {
            metric: _metric_difference(summary_a, summary_b, metric)
            for metric in ("visibility_score", "average_rank", "review_score")
        }
        return {
            "grid_a": {"grid_id": str(grid_id_a), "summary": summary_a},
            "grid_b": {"grid_id": str(grid_id_b), "summary": summary_b},
            "differences_a_minus_b": differences,
            "page_meta": result["meta"],
        }

    _register_rank_tool(
        server,
        api_client,
        tool_name="experimental_rank_grids_by_visibility",
        doc="Order grids by their current visibility score using a best-effort field match.",
        metric="visibility_score",
    )
    _register_rank_tool(
        server,
        api_client,
        tool_name="experimental_rank_grids_by_average_rank",
        doc="Order grids by their current average rank using a best-effort field match.",
        metric="average_rank",
    )
    _register_rank_tool(
        server,
        api_client,
        tool_name="experimental_rank_grids_by_review_score",
        doc="Order grids by their current review score using a best-effort field match.",
        metric="review_score",
    )

    @server.tool(structured_output=True)
    async def experimental_find_low_visibility_grids(
        threshold: Annotated[
            float,
            Field(description="Visibility score below which a grid is included"),
        ],
        limit: ResultLimit = 500,
        per_page: PerPage = 100,
    ) -> dict[str, Any]:
        """Find grids below a visibility threshold using a best-effort field match."""
        if not math.isfinite(threshold):
            raise ValueError("threshold must be a finite number")
        result = await api_client.list_all_grids(limit=limit, per_page=per_page)
        ranked, omitted = _rank_summaries(result["data"], "visibility_score", "ascending")
        below = [item for item in ranked if item["metric_value"] < threshold]
        return {
            "data": below,
            "meta": {
                **result["meta"],
                "threshold": threshold,
                "matched": len(below),
                "omitted_without_numeric_metric": omitted,
            },
        }

    @server.tool(structured_output=True)
    async def get_latest_grid_screenshot(grid_id: GridId) -> dict[str, Any]:
        """Get the newest grid screenshot with metric annotations."""
        return await _screenshot_result(api_client, str(grid_id), "with-metrics")

    @server.tool(structured_output=True)
    async def get_latest_grid_screenshot_without_metrics(grid_id: GridId) -> dict[str, Any]:
        """Get the newest clean grid screenshot without metric annotations."""
        return await _screenshot_result(api_client, str(grid_id), "without-metrics")

    @server.tool(structured_output=True)
    async def batch_get_latest_grid_screenshots(
        grid_ids: GridIds,
        mode: ScreenshotMode = "with-metrics",
    ) -> dict[str, Any]:
        """Get the newest screenshots for several grids and report any missing images."""
        for grid_id in grid_ids:
            path_id(grid_id, "grid_ids")
        results: list[dict[str, Any]] = []
        for grid_id in grid_ids:
            try:
                screenshot = await _screenshot_result(api_client, str(grid_id), mode)
            except WhitesparkAPIError as exc:
                if exc.status_code != 404:
                    raise
                results.append(
                    {
                        "grid_id": str(grid_id),
                        "mode": mode,
                        "status": "not_found",
                        "error": (
                            "Screenshot not found. Check the grid ID and available screenshots."
                        ),
                    }
                )
            else:
                results.append({**screenshot, "status": "available"})
        return {"results": results, "requested": len(grid_ids)}


def _register_rank_tool(
    server: MCPServer,
    api_client: WhitesparkClient,
    *,
    tool_name: str,
    doc: str,
    metric: str,
) -> None:
    async def rank_tool(
        direction: SortDirection = "descending",
        limit: ResultLimit = 100,
        per_page: PerPage = 100,
    ) -> dict[str, Any]:
        result = await api_client.list_all_grids(limit=limit, per_page=per_page)
        ranked, omitted = _rank_summaries(result["data"], metric, direction)
        return {
            "data": ranked,
            "meta": {
                **result["meta"],
                "metric": metric,
                "direction": direction,
                "omitted_without_numeric_metric": omitted,
            },
        }

    rank_tool.__doc__ = doc
    server.tool(name=tool_name, description=doc, structured_output=True)(rank_tool)


async def _screenshot_result(
    api_client: WhitesparkClient,
    grid_id: str,
    mode: Literal["with-metrics", "without-metrics"],
) -> dict[str, Any]:
    response = await api_client.get_latest_screenshot(grid_id=grid_id, mode=mode)
    content = response["content"]
    return {
        "grid_id": grid_id,
        "mode": mode,
        "content_type": response["content_type"],
        "content_disposition": response["content_disposition"],
        "size_bytes": len(content),
        "data_base64": base64.b64encode(content).decode("ascii"),
    }


def _normalized_field_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _summary_identifier(summary: Mapping[str, Any]) -> str | None:
    accepted = {"id", "gridid", "griduuid", "uuid"}
    for key, value in summary.items():
        if _normalized_field_name(key) in accepted and isinstance(value, str):
            return value
    return None


def _find_summary(summaries: list[Any], grid_id: str) -> dict[str, Any] | None:
    for summary in summaries:
        if isinstance(summary, dict) and _summary_identifier(summary) == grid_id:
            return summary
    return None


def _match_summaries(summaries: list[Any], grid_ids: list[str]) -> dict[str, dict[str, Any]]:
    requested = set(grid_ids)
    matches: dict[str, dict[str, Any]] = {}
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        identifier = _summary_identifier(summary)
        if identifier in requested:
            matches[identifier] = summary
    return matches


def _metric(summary: Mapping[str, Any], metric: str) -> tuple[str | None, float | None]:
    target = _normalized_field_name(metric)
    for key, value in summary.items():
        if _normalized_field_name(key) != target:
            continue
        if isinstance(value, bool) or not isinstance(value, Real):
            return key, None
        numeric = float(value)
        return key, numeric if math.isfinite(numeric) else None
    return None, None


def _metric_difference(
    summary_a: Mapping[str, Any], summary_b: Mapping[str, Any], metric: str
) -> dict[str, Any]:
    field_a, value_a = _metric(summary_a, metric)
    field_b, value_b = _metric(summary_b, metric)
    difference = value_a - value_b if value_a is not None and value_b is not None else None
    return {
        "value": difference,
        "grid_a_field": field_a,
        "grid_b_field": field_b,
    }


def _rank_summaries(
    summaries: list[Any], metric: str, direction: str
) -> tuple[list[dict[str, Any]], int]:
    ranked: list[dict[str, Any]] = []
    omitted = 0
    for summary in summaries:
        if not isinstance(summary, dict):
            omitted += 1
            continue
        field, value = _metric(summary, metric)
        if value is None:
            omitted += 1
            continue
        ranked.append({"summary": summary, "metric_field": field, "metric_value": value})
    ranked.sort(key=lambda item: item["metric_value"], reverse=direction == "descending")
    return ranked, omitted


def _requested_transport() -> str:
    return os.environ.get("WHITESPARK_MCP_TRANSPORT", "stdio").strip().lower() or "stdio"


def _host() -> str:
    return os.environ.get("WHITESPARK_MCP_HOST", "127.0.0.1").strip() or "127.0.0.1"


def _port() -> int:
    raw = os.environ.get("PORT", "8080").strip() or "8080"
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"PORT must be an integer, got {raw!r}") from None


def _csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def _transport_security() -> TransportSecuritySettings | None:
    """Origin checks are required off loopback; the SDK covers loopback itself."""
    host = _host()
    if host in _LOOPBACK_HOSTS:
        return None
    allowed_hosts = _csv_env("WHITESPARK_MCP_ALLOWED_HOSTS")
    if not allowed_hosts:
        raise SystemExit(
            "WHITESPARK_MCP_ALLOWED_HOSTS is required when WHITESPARK_MCP_HOST "
            f"is {host!r}. Set a comma-separated list of allowed Host values."
        )
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=_csv_env("WHITESPARK_MCP_ALLOWED_ORIGINS"),
    )


def create_serve_app() -> Starlette:
    """Build the stateless Streamable HTTP app. json_response stays at the SSE default."""
    mcp = create_server()
    return mcp.streamable_http_app(
        streamable_http_path="/mcp",
        host=_host(),
        stateless_http=True,
        transport_security=_transport_security(),
    )


async def _serve_streamable_http() -> None:
    import uvicorn

    config = uvicorn.Config(
        create_serve_app(),
        host=_host(),
        port=_port(),
        access_log=False,
    )
    await uvicorn.Server(config).serve()


def main() -> None:
    """Run stdio with deferred client configuration, or stateless Streamable HTTP."""
    transport = _requested_transport()
    if transport == "stdio":
        create_server().run(transport="stdio")
        return
    if transport == STREAMABLE_HTTP_TRANSPORT:
        asyncio.run(_serve_streamable_http())
        return
    raise SystemExit(
        "Unsupported WHITESPARK_MCP_TRANSPORT "
        f"{transport!r}; expected 'stdio' or '{STREAMABLE_HTTP_TRANSPORT}'."
    )


if __name__ == "__main__":
    main()
