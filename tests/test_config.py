"""Tests for environment-only server configuration."""

from __future__ import annotations

import pytest

from whitespark_mcp.config import (
    API_BASE_URL_ENV,
    API_TOKEN_ENV,
    ConfigError,
    WhitesparkConfig,
)


def test_missing_token_names_required_environment_variable() -> None:
    with pytest.raises(ConfigError, match=API_TOKEN_ENV):
        WhitesparkConfig.from_env({})


def test_missing_base_url_names_required_environment_variable() -> None:
    with pytest.raises(ConfigError, match=API_BASE_URL_ENV):
        WhitesparkConfig.from_env({API_TOKEN_ENV: "test-token"})


def test_configuration_loads_and_normalizes_values() -> None:
    config = WhitesparkConfig.from_env(
        {
            API_TOKEN_ENV: " test-token ",
            API_BASE_URL_ENV: "https://api.example.test/",
            "WHITESPARK_REQUEST_TIMEOUT_SECONDS": "12.5",
            "WHITESPARK_MAX_RETRIES": "2",
            "WHITESPARK_RETRY_BACKOFF_SECONDS": "0.5",
        }
    )

    assert config.api_token == "test-token"
    assert config.api_base_url == "https://api.example.test"
    assert config.request_timeout_seconds == 12.5
    assert config.max_retries == 2
    assert config.retry_backoff_seconds == 0.5


def test_startup_without_credentials_runs_stdio(monkeypatch, capsys) -> None:
    from whitespark_mcp import server
    from whitespark_mcp.runtime import MCPServer

    seen = []
    monkeypatch.setattr(MCPServer, "run", lambda self, **kwargs: seen.append(kwargs))
    server.main()
    assert seen == [{"transport": "stdio"}]
    assert "Traceback" not in capsys.readouterr().err
