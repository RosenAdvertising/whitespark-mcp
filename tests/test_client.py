"""Fully mocked tests for the Whitespark HTTP client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from whitespark_mcp.client import WhitesparkAPIError, WhitesparkClient
from whitespark_mcp.config import WhitesparkConfig

BASE_URL = "https://api.example.test"


def _config(**overrides: Any) -> WhitesparkConfig:
    values = {
        "api_token": "offline-test-token",
        "api_base_url": BASE_URL,
        "request_timeout_seconds": 5.0,
        "max_retries": 3,
        "retry_backoff_seconds": 0.0,
        **overrides,
    }
    return WhitesparkConfig(**values)


@pytest.mark.anyio
async def test_list_grids_happy_path_sends_bearer_auth_and_caps_page_size() -> None:
    payload = {
        "data": [{"id": "grid-1", "visibility_score": 42}],
        "meta": {"current_page": 2, "last_page": 2, "per_page": 100, "total": 1},
    }
    with respx.mock(assert_all_called=True) as router:
        route = router.get(f"{BASE_URL}/grids").mock(return_value=httpx.Response(200, json=payload))
        async with WhitesparkClient(_config()) as client:
            result = await client.list_grids_page(page=2, per_page=500)

    assert result == payload
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer offline-test-token"
    assert dict(request.url.params) == {"page": "2", "per_page": "100"}


@pytest.mark.anyio
async def test_auth_failure_surfaces_vendor_message() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{BASE_URL}/grids").mock(
            return_value=httpx.Response(401, json={"message": "Token is invalid or expired"})
        )
        async with WhitesparkClient(_config(max_retries=0)) as client:
            with pytest.raises(WhitesparkAPIError) as exc_info:
                await client.list_grids_page()

    assert exc_info.value.status_code == 401
    assert "Token is invalid or expired" in str(exc_info.value)


@pytest.mark.anyio
async def test_429_is_retried_and_honors_retry_after() -> None:
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    payload = {
        "data": [],
        "meta": {"current_page": 1, "last_page": 1, "per_page": 50, "total": 0},
    }
    with respx.mock(assert_all_called=True) as router:
        route = router.get(f"{BASE_URL}/grids").mock(
            side_effect=[
                httpx.Response(429, json={"message": "Slow down"}, headers={"Retry-After": "2"}),
                httpx.Response(200, json=payload),
            ]
        )
        async with WhitesparkClient(_config(), sleep=record_sleep) as client:
            result = await client.list_grids_page()

    assert result == payload
    assert route.call_count == 2
    assert delays == [2.0]


@pytest.mark.anyio
async def test_pagination_combines_pages_and_stops_at_limit() -> None:
    responses = [
        httpx.Response(
            200,
            json={
                "data": [{"id": "1"}, {"id": "2"}],
                "meta": {"current_page": 1, "last_page": 3, "per_page": 2, "total": 5},
            },
        ),
        httpx.Response(
            200,
            json={
                "data": [{"id": "3"}, {"id": "4"}],
                "meta": {"current_page": 2, "last_page": 3, "per_page": 2, "total": 5},
            },
        ),
        httpx.Response(
            200,
            json={
                "data": [{"id": "5"}],
                "meta": {"current_page": 3, "last_page": 3, "per_page": 1, "total": 5},
            },
        ),
    ]
    with respx.mock(assert_all_called=True) as router:
        route = router.get(f"{BASE_URL}/grids").mock(side_effect=responses)
        async with WhitesparkClient(_config()) as client:
            result = await client.list_all_grids(limit=5, per_page=2)

    assert [item["id"] for item in result["data"]] == ["1", "2", "3", "4", "5"]
    assert result["meta"] == {
        "returned": 5,
        "total": 5,
        "pages_fetched": 3,
        "last_page": 3,
        "requested_limit": 5,
        "truncated": False,
        "final_page_meta": {
            "current_page": 3,
            "last_page": 3,
            "per_page": 1,
            "total": 5,
        },
    }
    assert [call.request.url.params["page"] for call in route.calls] == ["1", "2", "3"]


@pytest.mark.anyio
async def test_5xx_exhaustion_surfaces_final_vendor_message() -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.get(f"{BASE_URL}/grids").mock(
            side_effect=[
                httpx.Response(503, json={"message": "First outage"}),
                httpx.Response(503, json={"message": "Still unavailable"}),
            ]
        )
        async with WhitesparkClient(_config(max_retries=1)) as client:
            with pytest.raises(WhitesparkAPIError, match="Still unavailable"):
                await client.list_grids_page()

    assert route.call_count == 2
