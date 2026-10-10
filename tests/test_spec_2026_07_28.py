"""Offline raw-wire regressions for the MCP 2026-07-28 migration."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import respx
from mcp import Client
from mcp.types import LATEST_PROTOCOL_VERSION
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from whitespark_mcp.server import create_server

PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSION = "2025-11-25"
PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_CAPABILITIES_META_KEY = "io.modelcontextprotocol/clientCapabilities"
CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"
REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://api.example.test"


def _modern_request(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    protocol_version: str = PROTOCOL_VERSION,
    request_id: int = 1,
) -> tuple[dict[str, str], dict[str, Any]]:
    request_params = dict(params or {})
    request_params["_meta"] = {
        PROTOCOL_VERSION_META_KEY: protocol_version,
        CLIENT_CAPABILITIES_META_KEY: {},
        CLIENT_INFO_META_KEY: {"name": "whitespark-spec-test", "version": "0"},
    }
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "mcp-protocol-version": protocol_version,
        "mcp-method": method,
    }
    if method in {"tools/call", "prompts/get"}:
        headers["mcp-name"] = str(request_params["name"])
    elif method == "resources/read":
        headers["mcp-name"] = str(request_params["uri"])
    return headers, {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": request_params,
    }


async def _post_modern(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    protocol_version: str = PROTOCOL_VERSION,
    header_overrides: dict[str, str] | None = None,
) -> httpx.Response:
    app = create_server().streamable_http_app(
        host="0.0.0.0",
        stateless_http=True,
        json_response=True,
    )
    headers, body = _modern_request(
        method,
        params,
        protocol_version=protocol_version,
    )
    if header_overrides:
        headers.update(header_overrides)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://spec-test",
        ) as client:
            return await client.post("/mcp", headers=headers, json=body)


def _result(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    return payload["result"]


def test_spec_check_pins_the_2026_revision() -> None:
    spec_check = REPO_ROOT / "tests" / "spec_check.py"
    if spec_check.is_file():
        result = subprocess.run(
            [sys.executable, str(spec_check)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Spec check: PASS" in result.stdout
    assert LATEST_PROTOCOL_VERSION == PROTOCOL_VERSION
    assert MODERN_PROTOCOL_VERSIONS == (PROTOCOL_VERSION,)


def test_modern_discovery_is_sessionless_and_declares_used_capabilities() -> None:
    response = asyncio.run(_post_modern("server/discover"))
    result = _result(response)

    assert "mcp-session-id" not in response.headers
    assert result["supportedVersions"] == [PROTOCOL_VERSION]
    assert result["resultType"] == "complete"
    assert result["ttlMs"] == 0
    assert result["cacheScope"] == "private"
    assert result["capabilities"] == {
        "prompts": {"listChanged": True},
        "resources": {"listChanged": True, "subscribe": True},
        "tools": {"listChanged": True},
    }
    assert "extensions" not in result["capabilities"]
    assert result["_meta"][SERVER_INFO_META_KEY]["name"] == "whitespark-mcp"
    assert result["_meta"][SERVER_INFO_META_KEY]["version"]


def test_client_negotiates_modern_and_keeps_legacy_compatibility() -> None:
    mcp_server = create_server()

    async def negotiate() -> tuple[str, str]:
        async with Client(mcp_server, cache=None) as modern:
            modern_version = modern.protocol_version
        async with Client(mcp_server, mode="legacy", cache=None) as legacy:
            legacy_version = legacy.protocol_version
        return modern_version, legacy_version

    modern_version, legacy_version = asyncio.run(negotiate())
    assert modern_version == PROTOCOL_VERSION
    assert legacy_version == LEGACY_PROTOCOL_VERSION


def test_cacheable_results_are_complete_private_and_deterministic() -> None:
    async def list_results() -> list[dict[str, Any]]:
        methods = (
            "tools/list",
            "tools/list",
            "prompts/list",
            "resources/list",
            "resources/templates/list",
        )
        return [_result(await _post_modern(method)) for method in methods]

    first_tools, second_tools, prompts, resources, templates = asyncio.run(list_results())
    for result in (first_tools, second_tools, prompts, resources, templates):
        assert result["resultType"] == "complete"
        assert result["ttlMs"] == 0
        assert result["cacheScope"] == "private"

    first_names = [tool["name"] for tool in first_tools["tools"]]
    second_names = [tool["name"] for tool in second_tools["tools"]]
    assert first_names == second_names
    assert len(first_names) == 12
    assert all(tool["inputSchema"]["type"] == "object" for tool in first_tools["tools"])
    assert prompts["prompts"] == []
    assert resources["resources"] == []
    assert templates["resourceTemplates"] == []


def test_resource_read_not_found_error() -> None:
    missing = asyncio.run(_post_modern("resources/read", {"uri": "whitespark://does-not-exist"}))
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == -32602


def test_modern_tool_results_are_structured_and_complete(monkeypatch: Any) -> None:
    monkeypatch.setenv("WHITESPARK_API_TOKEN", "offline-test-token")
    monkeypatch.setenv("WHITESPARK_API_BASE_URL", BASE_URL)
    page = {
        "data": [{"gridId": "grid-alpha", "name": "Grid Alpha"}],
        "meta": {"current_page": 1, "last_page": 1, "per_page": 50, "total": 1},
    }
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{BASE_URL}/grids").mock(return_value=httpx.Response(200, json=page))
        response = asyncio.run(
            _post_modern("tools/call", {"name": "list_ranking_grids", "arguments": {}})
        )
    result = _result(response)
    assert result["isError"] is False
    assert result["resultType"] == "complete"
    assert result["structuredContent"]["data"] == page["data"]
    assert "Grid Alpha" in result["content"][0]["text"]


def test_modern_http_enforces_routing_headers_versions_and_method_errors() -> None:
    named_headers, _ = _modern_request(
        "tools/call", {"name": "list_ranking_grids", "arguments": {}}
    )
    assert named_headers["mcp-protocol-version"] == PROTOCOL_VERSION
    assert named_headers["mcp-method"] == "tools/call"
    assert named_headers["mcp-name"] == "list_ranking_grids"

    mismatch = asyncio.run(
        _post_modern(
            "tools/list",
            header_overrides={"mcp-method": "resources/list"},
        )
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["error"]["code"] == -32020

    named_mismatch = asyncio.run(
        _post_modern(
            "tools/call",
            {"name": "list_ranking_grids", "arguments": {}},
            header_overrides={"mcp-name": "get_ranking_grid"},
        )
    )
    assert named_mismatch.status_code == 400
    assert named_mismatch.json()["error"]["code"] == -32020

    unsupported = asyncio.run(_post_modern("tools/list", protocol_version="2099-01-01"))
    assert unsupported.status_code == 400
    assert unsupported.json()["error"] == {
        "code": -32022,
        "message": "Unsupported protocol version",
        "data": {
            "supported": [PROTOCOL_VERSION],
            "requested": "2099-01-01",
        },
    }

    unknown = asyncio.run(_post_modern("example/unknown"))
    assert unknown.status_code == 404
    assert unknown.json()["error"] == {
        "code": -32601,
        "message": "Method not found",
        "data": "example/unknown",
    }
