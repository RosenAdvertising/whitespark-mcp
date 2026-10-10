"""Environment-only configuration for the Whitespark MCP server."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from whitespark_mcp.urls import require_https

API_TOKEN_ENV = "WHITESPARK_API_TOKEN"
API_BASE_URL_ENV = "WHITESPARK_API_BASE_URL"
REQUEST_TIMEOUT_ENV = "WHITESPARK_REQUEST_TIMEOUT_SECONDS"
MAX_RETRIES_ENV = "WHITESPARK_MAX_RETRIES"
RETRY_BACKOFF_ENV = "WHITESPARK_RETRY_BACKOFF_SECONDS"


class ConfigError(ValueError):
    """Raised when required server configuration is absent or invalid."""


@dataclass(frozen=True, slots=True)
class WhitesparkConfig:
    """Validated settings used to connect to Whitespark."""

    api_token: str
    api_base_url: str
    request_timeout_seconds: float = 30.0
    max_retries: int = 3
    retry_backoff_seconds: float = 0.25

    def __post_init__(self) -> None:
        try:
            require_https(self.api_base_url, API_BASE_URL_ENV)
        except ValueError as exc:
            raise ConfigError(str(exc)) from None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> WhitesparkConfig:
        """Load configuration exclusively from environment variables."""
        values = os.environ if environ is None else environ
        api_token = values.get(API_TOKEN_ENV, "").strip()
        if not api_token:
            raise ConfigError(
                f"Missing required environment variable {API_TOKEN_ENV}. "
                "Create a token in Whitespark Local Ranking Grids under Profile → API Access."
            )

        api_base_url = values.get(API_BASE_URL_ENV, "").strip().rstrip("/")
        if not api_base_url:
            raise ConfigError(
                f"Missing required environment variable {API_BASE_URL_ENV}. "
                "Set it to the API base URL confirmed for your Whitespark account; "
                "the public guide documents only relative routes."
            )
        timeout = _read_float(values, REQUEST_TIMEOUT_ENV, 30.0, minimum=0.01)
        retries = _read_int(values, MAX_RETRIES_ENV, 3, minimum=0, maximum=10)
        backoff = _read_float(values, RETRY_BACKOFF_ENV, 0.25, minimum=0.0)
        return cls(
            api_token=api_token,
            api_base_url=api_base_url,
            request_timeout_seconds=timeout,
            max_retries=retries,
            retry_backoff_seconds=backoff,
        )


def _read_float(values: Mapping[str, str], name: str, default: float, *, minimum: float) -> float:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name} must be a number.") from exc
    if value < minimum:
        raise ConfigError(f"Environment variable {name} must be at least {minimum}.")
    return value


def _read_int(
    values: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"Environment variable {name} must be between {minimum} and {maximum}.")
    return value
