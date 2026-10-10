"""Offline MCP round-trip tests for every advertised Whitespark tool."""

from __future__ import annotations

import base64
from importlib.metadata import version
from typing import Any

import httpx
import pytest
import respx
from mcp.client import Client

from whitespark_mcp.client import WhitesparkClient
from whitespark_mcp.config import WhitesparkConfig
from whitespark_mcp.server import PROTOCOL_VERSION, create_server

BASE_URL = "https://api.example.test"
ID_A = "11111111-1111-4111-8111-111111111111"
ID_B = "22222222-2222-4222-8222-222222222222"
ID_MISSING = "33333333-3333-4333-8333-333333333333"

SUMMARIES = [
    {
        "gridId": ID_A,
        "name": "Grid Alpha",
        "visibilityScore": 80.0,
        "average_rank": 2.5,
        "review-score": 4.8,
    },
    {
        "gridId": ID_B,
        "name": "Grid Beta",
        "visibilityScore": 35.0,
        "average_rank": 7.0,
        "review-score": 4.2,
    },
]


def _page_payload(data: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    items = SUMMARIES if data is None else data
    return {
        "data": items,
        "meta": {"current_page": 1, "last_page": 1, "per_page": 100, "total": len(items)},
    }


async def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    config = WhitesparkConfig(api_token="offline-test-token", api_base_url=BASE_URL)
    async with httpx.AsyncClient() as http_client:
        api_client = WhitesparkClient(config, http_client=http_client)
        server = create_server(client=api_client)
        async with Client(server, raise_exceptions=True) as mcp_client:
            result = await mcp_client.call_tool(name, arguments)
    assert result.structured_content is not None
    return result.structured_content


@pytest.mark.anyio
async def test_server_initialization_declares_2026_07_28_protocol() -> None:
    config = WhitesparkConfig(api_token="offline-test-token", api_base_url=BASE_URL)
    server = create_server(config=config)

    async with Client(server, raise_exceptions=True) as client:
        assert client.protocol_version == PROTOCOL_VERSION == "2026-07-28"

    major, minor, *_ = map(int, version("mcp").split("."))
    assert major == 2 and minor >= 2


@pytest.mark.anyio
async def test_every_tool_has_description_parameter_descriptions_and_output_schema() -> None:
    config = WhitesparkConfig(api_token="offline-test-token", api_base_url=BASE_URL)
    server = create_server(config=config)

    async with Client(server) as client:
        tools = (await client.list_tools()).tools

    assert len(tools) == 12
    for tool in tools:
        assert tool.name == tool.name.lower()
        assert tool.description and "\n" not in tool.description
        assert tool.output_schema is not None
        for schema in tool.input_schema.get("properties", {}).values():
            assert schema.get("description")


@pytest.mark.anyio
async def test_list_ranking_grids_round_trip() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{BASE_URL}/grids").mock(return_value=httpx.Response(200, json=_page_payload()))
        result = await _call_tool("list_ranking_grids", {"page": 1, "per_page": 100, "limit": 1})

    assert result["data"] == [SUMMARIES[0]]
    assert result["meta"]["returned"] == 1


@pytest.mark.anyio
async def test_list_all_ranking_grids_round_trip() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{BASE_URL}/grids").mock(return_value=httpx.Response(200, json=_page_payload()))
        result = await _call_tool("list_all_ranking_grids", {"limit": 10})

    assert result["data"] == SUMMARIES
    assert result["meta"]["pages_fetched"] == 1


@pytest.mark.anyio
async def test_experimental_get_ranking_grid_summary_round_trip() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{BASE_URL}/grids").mock(return_value=httpx.Response(200, json=_page_payload()))
        result = await _call_tool("experimental_get_ranking_grid_summary", {"grid_id": ID_A})

    assert result["summary"] == SUMMARIES[0]


@pytest.mark.anyio
async def test_experimental_batch_get_ranking_grid_summaries_round_trip() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{BASE_URL}/grids").mock(return_value=httpx.Response(200, json=_page_payload()))
        result = await _call_tool(
            "experimental_batch_get_ranking_grid_summaries",
            {"grid_ids": [ID_A, ID_MISSING]},
        )

    assert result["data"] == [SUMMARIES[0]]
    assert result["not_found"] == [ID_MISSING]


@pytest.mark.anyio
async def test_experimental_compare_grid_summaries_round_trip() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{BASE_URL}/grids").mock(return_value=httpx.Response(200, json=_page_payload()))
        result = await _call_tool(
            "experimental_compare_grid_summaries",
            {"grid_id_a": ID_A, "grid_id_b": ID_B},
        )

    assert result["differences_a_minus_b"]["visibility_score"]["value"] == 45.0
    assert result["differences_a_minus_b"]["average_rank"]["value"] == -4.5
    assert result["differences_a_minus_b"]["review_score"]["value"] == pytest.approx(0.6)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool_name", "metric_name", "expected_first"),
    [
        ("experimental_rank_grids_by_visibility", "visibility_score", ID_A),
        ("experimental_rank_grids_by_average_rank", "average_rank", ID_B),
        ("experimental_rank_grids_by_review_score", "review_score", ID_A),
    ],
)
async def test_experimental_rank_tool_round_trip(
    tool_name: str, metric_name: str, expected_first: str
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{BASE_URL}/grids").mock(return_value=httpx.Response(200, json=_page_payload()))
        result = await _call_tool(tool_name, {"direction": "descending", "limit": 10})

    assert result["meta"]["metric"] == metric_name
    assert result["data"][0]["summary"]["gridId"] == expected_first


@pytest.mark.anyio
async def test_experimental_find_low_visibility_grids_round_trip() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{BASE_URL}/grids").mock(return_value=httpx.Response(200, json=_page_payload()))
        result = await _call_tool(
            "experimental_find_low_visibility_grids", {"threshold": 50, "limit": 10}
        )

    assert [item["summary"]["gridId"] for item in result["data"]] == [ID_B]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool_name", "mode"),
    [
        ("get_latest_grid_screenshot", "with-metrics"),
        ("get_latest_grid_screenshot_without_metrics", "without-metrics"),
    ],
)
async def test_single_screenshot_tool_round_trip(tool_name: str, mode: str) -> None:
    screenshot = b"offline-png-bytes"
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{BASE_URL}/grid/{ID_A}/download-screenshot/{mode}").mock(
            return_value=httpx.Response(
                200,
                content=screenshot,
                headers={"Content-Type": "image/png", "Content-Disposition": "inline"},
            )
        )
        result = await _call_tool(tool_name, {"grid_id": ID_A})

    assert result["mode"] == mode
    assert result["content_type"] == "image/png"
    assert result["data_base64"] == base64.b64encode(screenshot).decode("ascii")


@pytest.mark.anyio
async def test_batch_screenshot_tool_reports_per_grid_404_round_trip() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{BASE_URL}/grid/{ID_A}/download-screenshot/with-metrics").mock(
            return_value=httpx.Response(
                200,
                content=b"image",
                headers={"Content-Type": "image/png"},
            )
        )
        router.get(f"{BASE_URL}/grid/{ID_MISSING}/download-screenshot/with-metrics").mock(
            return_value=httpx.Response(404, json={"message": "No screenshot exists"})
        )
        result = await _call_tool(
            "batch_get_latest_grid_screenshots",
            {"grid_ids": [ID_A, ID_MISSING], "mode": "with-metrics"},
        )

    assert [item["status"] for item in result["results"]] == ["available", "not_found"]
    assert "Screenshot not found" in result["results"][1]["error"]
    assert "No screenshot exists" not in result["results"][1]["error"]
