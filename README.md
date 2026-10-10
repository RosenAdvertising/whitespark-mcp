# Whitespark MCP server

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/RosenAdvertising/whitespark-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/RosenAdvertising/whitespark-mcp/actions/workflows/ci.yml)

A read-only [Model Context Protocol](https://modelcontextprotocol.io/) server for retrieving Whitespark Local Ranking Grids summaries and screenshots. It runs over stdio and works with Claude Desktop and other MCP-compatible clients.

## Features

The server exposes 12 tools:

- List a page of ranking grids or traverse pages up to a caller-selected result limit.
- Find one or more grid summaries by ID and compare documented visibility, average-rank, and review-score metrics.
- Rank fetched summaries by visibility, average rank, or review score, and find grids below a visibility threshold.
- Retrieve the latest screenshot with or without metrics, individually or in a batch. Batch results report missing screenshots per grid.

Summary lookup and metric tools prefixed with `experimental_` use best-effort matching because the public beta guide does not specify the summary identifier and metric property names. They recognize common casing and separators and omit unavailable numeric metrics rather than inventing values.

All operations are reads. Whitespark's public guide does not document grid creation or modification, so the server has no write tools. Requests use the documented relative routes. The API base URL is not published in the guide and must be provided for your account; the server does not guess a default.

## Requirements

- Python `>=3.12,<3.13`
- [uv](https://docs.astral.sh/uv/)
- A Whitespark Local Ranking Grids account and API bearer token
- An MCP-compatible client

## Install from source

Clone this repository, then install its locked dependencies:

```bash
uv sync --locked
```

## Configure

Create a token in Local Ranking Grids under **Profile → API Access**. Set these environment variables for the server process:

| Variable | Required | Description |
| --- | --- | --- |
| `WHITESPARK_API_TOKEN` | Yes | Bearer token from API Access. |
| `WHITESPARK_API_BASE_URL` | Yes | Exact API base URL confirmed for your account. |
| `WHITESPARK_REQUEST_TIMEOUT_SECONDS` | No | Request timeout in seconds; defaults to `30`. |
| `WHITESPARK_MAX_RETRIES` | No | Retry count for HTTP 429 and 5xx responses; defaults to `3`. |
| `WHITESPARK_RETRY_BACKOFF_SECONDS` | No | Exponential retry backoff base in seconds; defaults to `0.25`. |

For local development, copy `.env.example` to `.env` and replace its placeholders. The server does not load `.env` automatically; export its values through your shell or process manager. Keep the populated `.env` file private and out of version control.

## MCP client configuration

For Claude Desktop or another client that accepts a JSON MCP server configuration, use the path where you cloned this repository and provide your account settings through the client environment:

```json
{
  "mcpServers": {
    "whitespark": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/whitespark-mcp", "whitespark-mcp"],
      "env": {
        "WHITESPARK_API_TOKEN": "your-api-token",
        "WHITESPARK_API_BASE_URL": "https://your-confirmed-api-base.example"
      }
    }
  }
}
```

Replace `/path/to/whitespark-mcp` and both example values with your local path and account configuration. Avoid placing real credentials in shared or committed configuration files.

## HTTP mode

Stdio stays the default. Set `WHITESPARK_MCP_TRANSPORT=streamable-http` to serve stateless Streamable HTTP for MCP 2026-07-28. Clients POST to `/mcp`.

> **Security: this endpoint has no authentication and no TLS.** Anyone who can reach the port can run every tool, including write and delete tools, with this server's vendor credentials. Keep the default loopback bind (`127.0.0.1`), or put the server behind an authenticating TLS proxy on a private network. `WHITESPARK_MCP_ALLOWED_HOSTS` and `WHITESPARK_MCP_ALLOWED_ORIGINS` protect against browser DNS rebinding, not against direct callers. A proxy in front of it needs connection and idle timeouts: a legacy-style `GET /mcp` with `Accept: text/event-stream` holds a stream open until the client disconnects.

| Variable | Required | Description |
| --- | --- | --- |
| `WHITESPARK_MCP_TRANSPORT` | No | `stdio` (default) or `streamable-http`. |
| `WHITESPARK_MCP_HOST` | No | Bind address. Defaults to `127.0.0.1`. |
| `PORT` | No | Bind port. Defaults to `8080`. Must be an integer. |
| `WHITESPARK_MCP_ALLOWED_HOSTS` | When the host is not loopback | Comma-separated Host values allowed by DNS-rebinding protection. |
| `WHITESPARK_MCP_ALLOWED_ORIGINS` | No | Comma-separated Origin values. Read when the host is not loopback. |
| `WHITESPARK_API_TOKEN` | Yes | Bearer token from API Access. Same variable as stdio. |
| `WHITESPARK_API_BASE_URL` | Yes | Exact API base URL confirmed for your account. |
| `WHITESPARK_REQUEST_TIMEOUT_SECONDS` | No | Request timeout in seconds; defaults to `30`. |
| `WHITESPARK_MAX_RETRIES` | No | Retry count for HTTP 429 and 5xx responses; defaults to `3`. |
| `WHITESPARK_RETRY_BACKOFF_SECONDS` | No | Exponential retry backoff base in seconds; defaults to `0.25`. |

Vendor credentials come from those environment variables, never from the HTTP request.

```bash
WHITESPARK_MCP_TRANSPORT=streamable-http \
WHITESPARK_API_TOKEN=your-api-token \
WHITESPARK_API_BASE_URL=https://your-confirmed-api-base.example \
uv run --directory /path/to/whitespark-mcp whitespark-mcp
```

That listens on `127.0.0.1:8080` and accepts MCP requests at `http://127.0.0.1:8080/mcp`. A non-loopback `WHITESPARK_MCP_HOST` also needs `WHITESPARK_MCP_ALLOWED_HOSTS`.

## API behavior

- Grid page sizes are capped at the documented maximum of 100. List tools also require a local result limit to prevent unbounded traversal.
- HTTP 429 and 5xx responses are retried with exponential backoff. A numeric `Retry-After` value is honored up to 60 seconds; the default is at most three retries. Whitespark does not publish a numeric rate limit.
- Non-success responses become tool errors that include Whitespark's message when present.
- A screenshot 404 is an error for an individual fetch and a per-grid `not_found` result in a batch.

## Development

The test suite mocks all HTTP requests and does not contact Whitespark.

```bash
uv run --locked pytest -q
uv run --locked ruff check .
```

## References

- [Whitespark Local Ranking Grids API guide](https://lrg.whitespark.ca/api-docs/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [MCP specification, version 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28)

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).

## Local setup and verification

The server starts and answers MCP initialization and tool listing without credentials.
Tools report a configuration error until the required environment is provided.
Run `whitespark-mcp-setup` for environment-variable instructions, then restart your MCP
launcher with those variables exported. Run `whitespark-mcp-verify` to validate local
configuration (exit 2 if missing or invalid). This command does not contact the
vendor or prove authentication. Neither command saves or prints secret values.

`WHITESPARK_API_BASE_URL` must use HTTPS without userinfo, query or fragment.
Automatic redirects are disabled to keep bearer credentials at the configured destination.
