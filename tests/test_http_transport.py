"""In-process Streamable HTTP checks against create_serve_app()."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from mcp.client import Client

from whitespark_mcp.server import (
    MCPServer,
    _host,
    _port,
    _requested_transport,
    create_serve_app,
    create_server,
    main,
)

PROTOCOL_VERSION = "2026-07-28"
PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_CAPABILITIES_META_KEY = "io.modelcontextprotocol/clientCapabilities"
CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"
BASE_URL = "https://api.example.test"
LOOPBACK = "http://127.0.0.1:8080"
REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clear_http_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "WHITESPARK_MCP_TRANSPORT",
        "WHITESPARK_MCP_HOST",
        "WHITESPARK_MCP_ALLOWED_HOSTS",
        "WHITESPARK_MCP_ALLOWED_ORIGINS",
        "PORT",
    ):
        monkeypatch.delenv(name, raising=False)


def _modern_request(
    method: str,
    params: dict[str, Any] | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    request_params = dict(params or {})
    request_params["_meta"] = {
        PROTOCOL_VERSION_META_KEY: PROTOCOL_VERSION,
        CLIENT_CAPABILITIES_META_KEY: {},
        CLIENT_INFO_META_KEY: {"name": "whitespark-http-test", "version": "0"},
    }
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "mcp-protocol-version": PROTOCOL_VERSION,
        "mcp-method": method,
    }
    if method == "tools/call":
        headers["mcp-name"] = str(request_params["name"])
    return headers, {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": request_params,
    }


def _json_body(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        data_lines = [
            line.removeprefix("data:").strip()
            for line in response.text.splitlines()
            if line.startswith("data:")
        ]
        assert data_lines, response.text
        payload = json.loads(data_lines[-1])
    else:
        payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _result(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    payload = _json_body(response)
    assert payload["jsonrpc"] == "2.0"
    return payload["result"]


@asynccontextmanager
async def _http_client(
    app: Any,
    *,
    base_url: str = LOOPBACK,
) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
            yield client


async def _post(
    client: httpx.AsyncClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    header_overrides: Mapping[str, str] | None = None,
) -> httpx.Response:
    headers, body = _modern_request(method, params)
    if header_overrides:
        headers.update(header_overrides)
    return await client.post("/mcp", headers=headers, json=body)


def _input_schemas(tools: list[Any]) -> dict[str, Any]:
    schemas: dict[str, Any] = {}
    for tool in tools:
        if isinstance(tool, dict):
            schemas[tool["name"]] = tool["inputSchema"]
            continue
        dumped = tool.model_dump(mode="json", by_alias=True)
        schemas[dumped["name"]] = dumped["inputSchema"]
    return schemas


def test_default_transport_is_stdio() -> None:
    assert _requested_transport() == "stdio"


def test_bogus_transport_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHITESPARK_MCP_TRANSPORT", " Bogus ")
    with pytest.raises(SystemExit) as exc:
        main()
    message = str(exc.value)
    assert "stdio" in message
    assert "streamable-http" in message


def test_non_integer_port_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "nope")
    with pytest.raises(SystemExit, match="PORT"):
        _port()


def test_non_loopback_host_without_allowed_hosts_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHITESPARK_MCP_HOST", "0.0.0.0")
    with pytest.raises(SystemExit, match="WHITESPARK_MCP_ALLOWED_HOSTS"):
        create_serve_app()


def test_empty_transport_selects_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []

    class StubServer:
        def run(self, *, transport: str) -> None:
            started.append(transport)

    monkeypatch.setattr("whitespark_mcp.server.create_server", lambda: StubServer())
    for raw in ("", " ", "\t"):
        monkeypatch.setenv("WHITESPARK_MCP_TRANSPORT", raw)
        assert _requested_transport() == "stdio"
        main()
    assert started == ["stdio"] * 3


def test_empty_host_defaults_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    for raw in ("", " "):
        monkeypatch.setenv("WHITESPARK_MCP_HOST", raw)
        assert _host() == "127.0.0.1"
        create_serve_app()


def test_uppercase_localhost_is_not_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHITESPARK_MCP_HOST", "LOCALHOST")
    assert _host() == "LOCALHOST"
    with pytest.raises(SystemExit, match="WHITESPARK_MCP_ALLOWED_HOSTS"):
        create_serve_app()


def test_importing_server_without_installed_distribution_succeeds() -> None:
    """A checkout whose distribution metadata is missing must still import the server."""
    code = (
        "import importlib.metadata as metadata\n"
        "real_version = metadata.version\n"
        "def version_without_whitespark(name):\n"
        "    if name.replace('_', '-').lower() == 'whitespark-mcp':\n"
        "        raise metadata.PackageNotFoundError(name)\n"
        "    return real_version(name)\n"
        "metadata.version = version_without_whitespark\n"
        "import whitespark_mcp.server\n"
        "print('whitespark-server-import-ok')\n"
    )
    env = {**os.environ, "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring"}
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "whitespark-server-import-ok" in result.stdout


@pytest.mark.anyio
async def test_tools_list_matches_stdio_server() -> None:
    stdio_server = create_server()
    async with Client(stdio_server) as client:
        stdio_tools = (await client.list_tools()).tools

    app = create_serve_app()
    async with _http_client(app) as client:
        response = await _post(client, "tools/list")

    result = _result(response)
    assert [tool.name for tool in stdio_tools] == [tool["name"] for tool in result["tools"]]
    assert _input_schemas(list(stdio_tools)) == _input_schemas(result["tools"])


@pytest.mark.anyio
async def test_read_tool_runs_over_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHITESPARK_API_TOKEN", "offline-test-token")
    monkeypatch.setenv("WHITESPARK_API_BASE_URL", BASE_URL)
    page = {
        "data": [{"gridId": "grid-alpha", "name": "Grid Alpha"}],
        "meta": {"current_page": 1, "last_page": 1, "per_page": 100, "total": 1},
    }
    app = create_serve_app()
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{BASE_URL}/grids").mock(return_value=httpx.Response(200, json=page))
        async with _http_client(app) as client:
            response = await _post(
                client,
                "tools/call",
                {
                    "name": "list_ranking_grids",
                    "arguments": {"page": 1, "per_page": 100, "limit": 1},
                },
            )
    result = _result(response)
    assert result["isError"] is False
    assert result["structuredContent"]["data"] == page["data"]
    assert result["structuredContent"]["meta"]["returned"] == 1


@pytest.mark.anyio
async def test_responses_have_no_session_and_requests_share_none() -> None:
    app = create_serve_app()
    async with _http_client(app) as client:
        first = await _post(client, "tools/list")
        second = await _post(
            client,
            "tools/list",
            header_overrides={"mcp-session-id": "not-a-session"},
        )
    first_result = _result(first)
    second_result = _result(second)
    assert "mcp-session-id" not in first.headers
    assert "mcp-session-id" not in second.headers
    assert [tool["name"] for tool in first_result["tools"]] == [
        tool["name"] for tool in second_result["tools"]
    ]


@pytest.mark.anyio
async def test_allowed_hosts_refuses_other_host_and_bad_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WHITESPARK_MCP_HOST", "10.1.2.3")
    monkeypatch.setenv("WHITESPARK_MCP_ALLOWED_HOSTS", "10.1.2.3:*")
    monkeypatch.setenv("WHITESPARK_MCP_ALLOWED_ORIGINS", "http://10.1.2.3:*")
    app = create_serve_app()
    async with _http_client(app, base_url="http://10.1.2.3:8080") as client:
        foreign_host = await _post(
            client,
            "tools/list",
            header_overrides={"host": "evil.example"},
        )
        bad_origin = await _post(
            client,
            "tools/list",
            header_overrides={"origin": "http://evil.example"},
        )
    assert foreign_host.status_code == 421
    assert bad_origin.status_code == 403


@pytest.mark.anyio
async def test_get_and_delete_are_method_not_allowed_and_discover_names_version() -> None:
    app = create_serve_app()
    headers = {
        "accept": "application/json, text/event-stream",
        "mcp-protocol-version": PROTOCOL_VERSION,
        "host": "127.0.0.1:8080",
    }
    async with _http_client(app) as client:
        get_response = await client.get("/mcp", headers=headers)
        delete_response = await client.delete("/mcp", headers=headers)
        discover = await _post(client, "server/discover")
    assert get_response.status_code == 405
    assert delete_response.status_code == 405
    result = _result(discover)
    assert PROTOCOL_VERSION in result["supportedVersions"]
    assert result["_meta"][SERVER_INFO_META_KEY]["version"]


@pytest.mark.anyio
async def test_stateless_lifespan_runs_once_per_app() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[dict[str, Any]]:
        events.append("enter")
        try:
            yield {}
        finally:
            events.append("exit")

    mcp_server = MCPServer(
        name="whitespark-mcp",
        title="Lifespan probe",
        version="0.1.0",
        lifespan=lifespan,
    )
    app = mcp_server.streamable_http_app(
        streamable_http_path="/mcp",
        host="127.0.0.1",
        stateless_http=True,
        json_response=True,
    )
    async with _http_client(app) as client:
        first = await _post(client, "tools/list")
        second = await _post(client, "tools/list")
        assert first.status_code == 200
        assert second.status_code == 200
        assert events == ["enter"]
    assert events == ["enter", "exit"]
