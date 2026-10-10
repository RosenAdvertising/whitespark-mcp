# Whitespark MCP server

[![CI](https://github.com/RosenAdvertising/whitespark-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/RosenAdvertising/whitespark-mcp/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP 2026-07-28](https://img.shields.io/badge/MCP-2026--07--28-7C3AED.svg)](https://modelcontextprotocol.io)

Give Claude and other MCP clients read-only access to Whitespark Local Ranking Grid summaries and screenshots.

Whitespark MCP server is a [Model Context Protocol](https://modelcontextprotocol.io) server for [Whitespark](https://whitespark.ca/) Local Ranking Grids. It registers 12 tools, all read-only. It runs over stdio by default, for desktop clients such as Claude Desktop, and offers an opt-in stateless Streamable HTTP mode that implements MCP specification 2026-07-28. Whitespark credentials stay on the machine that runs the server: the API token and base URL come from environment variables, never from tool arguments or requests.

## Features

- List ranking grids one page at a time, or across pages up to a result limit you choose.
- Look up grid summaries by ID and compare visibility, average-rank and review-score metrics.
- Rank fetched summaries by visibility, average rank or review score, and find grids below a visibility threshold.
- Retrieve the latest grid screenshot with or without metrics, for one grid or a batch. Batch results report missing screenshots per grid.

## Tools

The server registers 12 tools, all read-only. Whitespark's public guide does not document grid creation or modification, so the server has no write tools.

| Tool | What it does |
| --- | --- |
| `list_ranking_grids` | Show one page of the ranking grids configured in your Whitespark account. |
| `list_all_ranking_grids` | Show ranking grids across pages, up to the limit you choose. |
| `experimental_get_ranking_grid_summary` | Find one grid on a chosen page using a best-effort identifier-field match. |
| `experimental_batch_get_ranking_grid_summaries` | Find several grids on a chosen page using best-effort identifier-field matches. |
| `experimental_compare_grid_summaries` | Compare the current visibility, average-rank and review-score metrics for two grids on a chosen page. |
| `experimental_rank_grids_by_visibility` | Order grids by their current visibility score. |
| `experimental_rank_grids_by_average_rank` | Order grids by their current average rank. |
| `experimental_rank_grids_by_review_score` | Order grids by their current review score. |
| `experimental_find_low_visibility_grids` | Find grids whose visibility score is below a threshold. |
| `get_latest_grid_screenshot` | Get the newest grid screenshot with metric annotations. |
| `get_latest_grid_screenshot_without_metrics` | Get the newest clean grid screenshot without metric annotations. |
| `batch_get_latest_grid_screenshots` | Get the newest screenshots for up to 25 grids and report any missing images. |

Tools whose names start with `experimental_` read the grid list, whose summary identifier and metric field names the public beta guide does not specify. They match those fields on a best-effort basis, ignoring case and separators, and leave out grids that have no numeric value for a metric rather than inventing one.

## Requirements

- Python `>=3.12,<3.13`
- [uv](https://docs.astral.sh/uv/)
- A Whitespark Local Ranking Grids account and API bearer token
- An MCP client such as Claude Desktop

## Installation

Install [uv](https://docs.astral.sh/uv/), then clone the repository and install its locked dependencies:

```bash
git clone https://github.com/RosenAdvertising/whitespark-mcp.git
cd whitespark-mcp
uv sync --locked
```

## Configuration

Create a token in Local Ranking Grids under **Profile → API Access**. Set these environment variables for the server process:

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `WHITESPARK_API_TOKEN` | Yes | none | Bearer token from API Access. |
| `WHITESPARK_API_BASE_URL` | Yes | none | Exact API base URL confirmed for your account. Must be a complete HTTPS URL without userinfo, query or fragment. |
| `WHITESPARK_REQUEST_TIMEOUT_SECONDS` | No | `30` | Request timeout in seconds; at least `0.01`. |
| `WHITESPARK_MAX_RETRIES` | No | `3` | Retries for HTTP 429 and 5xx responses; an integer from 0 to 10. |
| `WHITESPARK_RETRY_BACKOFF_SECONDS` | No | `0.25` | Exponential retry backoff base in seconds; zero or greater. |

Whitespark's public guide does not publish the API base URL, so the server does not guess a default. Automatic redirects are disabled to keep the bearer token at the configured destination.

For local development, copy `.env.example` to `.env` and replace its placeholders. The server does not load `.env` automatically; export its values through your shell or process manager. Keep the populated `.env` file private and out of version control.

Initialization and tool listing work without credentials. Tool calls return a configuration error until both required variables are set. Two commands help with local setup:

```bash
uv run whitespark-mcp-setup
uv run whitespark-mcp-verify
```

`whitespark-mcp-setup` prints instructions for the environment variables: export them in the environment your MCP client uses, then restart the client. `whitespark-mcp-verify` checks the local configuration and exits with code 2 if it is missing or invalid. It does not contact Whitespark or prove authentication. Neither command saves or prints secret values.

## Usage with Claude Desktop

Add the server to Claude Desktop's configuration file (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "whitespark": {
      "command": "uv",
      "args": ["run", "--locked", "--directory", "/absolute/path/to/whitespark-mcp", "whitespark-mcp"],
      "env": {
        "WHITESPARK_API_TOKEN": "replace-with-your-api-token",
        "WHITESPARK_API_BASE_URL": "https://replace-with-confirmed-api-base.example"
      }
    }
  }
}
```

Replace `/absolute/path/to/whitespark-mcp` with the path of your clone and both example values with your account settings, then restart Claude Desktop. Keep real credentials in your client's protected environment or secret store. Any other stdio MCP client uses the same command and arguments.

## HTTP mode

Stdio is the default. Set `WHITESPARK_MCP_TRANSPORT=streamable-http` to serve the stateless Streamable HTTP transport from MCP specification 2026-07-28 at `/mcp`. Each request stands alone: no initialization handshake and no `Mcp-Session-Id`. Clients on earlier protocol versions are served on the same endpoint.

> **Security: this endpoint has no authentication and no TLS.** Anyone who can reach the port can run every tool with this server's vendor credentials. Keep the default loopback bind (`127.0.0.1`), or put the server behind an authenticating TLS proxy on a private network. `WHITESPARK_MCP_ALLOWED_HOSTS` and `WHITESPARK_MCP_ALLOWED_ORIGINS` protect against browser DNS rebinding, not against direct callers. A proxy in front of it needs connection and idle timeouts: a legacy-style `GET /mcp` with `Accept: text/event-stream` holds a stream open until the client disconnects.

| Variable | Default | Purpose |
| --- | --- | --- |
| `WHITESPARK_MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http`. An empty value selects `stdio`; any other value exits. |
| `WHITESPARK_MCP_HOST` | `127.0.0.1` | Bind address. `127.0.0.1`, `localhost` and `::1` use the SDK's built-in Host and Origin checks; any other value requires `WHITESPARK_MCP_ALLOWED_HOSTS`. |
| `PORT` | `8080` | Port; must be an integer (an empty value uses `8080`). |
| `WHITESPARK_MCP_ALLOWED_HOSTS` | unset | Comma-separated `Host` header values accepted on a non-loopback bind, such as `mcp.example.com:8080` or `mcp.example.com:*`. The server exits if it is unset on a non-loopback bind. |
| `WHITESPARK_MCP_ALLOWED_ORIGINS` | unset | Comma-separated `Origin` values accepted on a non-loopback bind, such as `https://client.example.com`. Requests without an `Origin` header are accepted. |

Whitespark credentials come from the same configuration as stdio (see [Configuration](#configuration)), never from the request.

```bash
WHITESPARK_MCP_TRANSPORT=streamable-http PORT=8080 uv run --locked whitespark-mcp
```

Point the MCP client at `http://127.0.0.1:8080/mcp`.

## Error handling

A failed tool call returns an MCP error result (`isError`) with a fixed message. Whitespark's own error text and response bodies are never returned or logged.

- Missing or invalid configuration: a message that names `WHITESPARK_API_TOKEN`, `WHITESPARK_API_BASE_URL` and `whitespark-mcp-setup`. This includes an invalid timeout, retry or backoff value, which is reported when a tool is called, not at startup.
- HTTP 401, 403, 404 and 429: a short message that says what to check (credentials, account permissions, the record ID, or waiting before a retry).
- HTTP 5xx: a "Service unavailable" message.
- Any other HTTP status, including a redirect (redirects are never followed): "Request rejected or response invalid. Check inputs and API configuration."
- Connection failures and timeouts: "Connection failed; the operation outcome may be unknown."
- Invalid tool input: "Invalid tool input or response."
- Any other failure, including a grid list response that does not have the expected shape: "The operation could not be completed."

`experimental_get_ranking_grid_summary` and `experimental_compare_grid_summaries` return the invalid-input message when a grid is not on the requested page; pass `page` to search another page. `experimental_batch_get_ranking_grid_summaries` lists such IDs under `not_found` instead.

A screenshot that returns HTTP 404 is a "Resource not found" error for `get_latest_grid_screenshot` and `get_latest_grid_screenshot_without_metrics`. In `batch_get_latest_grid_screenshots` it becomes a per-grid `not_found` result; any other error fails the whole call.

HTTP 429 and 5xx responses are retried up to `WHITESPARK_MAX_RETRIES` times (default 3). The delay starts at `WHITESPARK_RETRY_BACKOFF_SECONDS` and doubles on each retry, capped at 60 seconds. A numeric `Retry-After` header sets the delay instead, up to 60 seconds. Connection failures and timeouts are not retried. Whitespark does not publish a numeric rate limit.

The server exits with status 1 and a message that names the setting when `WHITESPARK_MCP_TRANSPORT` is unsupported, `PORT` is not an integer, or `WHITESPARK_MCP_HOST` is not a loopback address and `WHITESPARK_MCP_ALLOWED_HOSTS` is unset.

## API behavior

- Grid page sizes are capped at the documented maximum of 100. List tools also require a local result limit to prevent unbounded traversal.
- Requests use the documented relative routes under the configured API base URL.

## References

- [Whitespark Local Ranking Grids API guide](https://lrg.whitespark.ca/api-docs/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [MCP specification, version 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28)

## Testing

The test suite runs offline and needs no Whitespark account: every Whitespark API call is intercepted at the HTTP layer with `respx`. It covers each tool's requests and responses, input validation, pagination and retries, base URL validation and redirect handling, error handling that withholds vendor text, configuration loading, and the Streamable HTTP transport including the 2026-07-28 wire format, Host and Origin checks and stateless requests.

```bash
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check .
```

CI runs the suite on every push and pull request to `main`.

## License

MIT. See [LICENSE](LICENSE).
