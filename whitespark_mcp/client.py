"""Thin asynchronous HTTP client for the documented Whitespark API routes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx

from whitespark_mcp.config import WhitesparkConfig
from whitespark_mcp.paths import path_id

MAX_PAGE_SIZE = 100


class WhitesparkError(RuntimeError):
    """Base exception for errors produced while communicating with Whitespark."""


class WhitesparkAPIError(WhitesparkError):
    """An HTTP error returned by Whitespark with its message preserved."""

    def __init__(self, status_code: int, vendor_message: str) -> None:
        self.status_code = status_code
        self.vendor_message = vendor_message
        super().__init__(f"Whitespark API returned HTTP {status_code}: {vendor_message}")


class WhitesparkResponseError(WhitesparkError):
    """A successful response that does not match the documented response envelope."""


class WhitesparkClient:
    """Call the two documented Whitespark GET routes with retries and pagination."""

    def __init__(
        self,
        config: WhitesparkConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._config = config
        self._sleep = sleep
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            timeout=config.request_timeout_seconds,
            follow_redirects=False,
        )

    async def __aenter__(self) -> WhitesparkClient:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the internally owned HTTP connection pool."""
        if self._owns_http_client:
            await self._http_client.aclose()

    async def list_grids_page(self, *, page: int = 1, per_page: int = 50) -> dict[str, Any]:
        """Return one validated page from GET /grids."""
        if page < 1:
            raise ValueError("page must be at least 1")
        if per_page < 1:
            raise ValueError("per_page must be at least 1")
        capped_per_page = min(per_page, MAX_PAGE_SIZE)
        response = await self._request(
            "GET",
            "/grids",
            params={"page": page, "per_page": capped_per_page},
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise WhitesparkResponseError(
                "Whitespark GET /grids returned a non-JSON success response."
            ) from exc
        if not isinstance(payload, dict):
            raise WhitesparkResponseError("Whitespark GET /grids response must be a JSON object.")
        data = payload.get("data")
        meta = payload.get("meta")
        if not isinstance(data, list) or not isinstance(meta, dict):
            raise WhitesparkResponseError(
                "Whitespark GET /grids response must contain a data array and meta object."
            )
        return {"data": data, "meta": meta}

    async def list_all_grids(self, *, limit: int = 500, per_page: int = 100) -> dict[str, Any]:
        """Traverse GET /grids pages until the requested local limit or final page."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if per_page < 1:
            raise ValueError("per_page must be at least 1")

        grids: list[Any] = []
        page = 1
        pages_fetched = 0
        final_meta: dict[str, Any] = {}
        while len(grids) < limit:
            request_size = min(per_page, MAX_PAGE_SIZE, limit - len(grids))
            result = await self.list_grids_page(page=page, per_page=request_size)
            pages_fetched += 1
            page_data = result["data"]
            final_meta = result["meta"]
            grids.extend(page_data[: limit - len(grids)])

            current_page = _meta_int(final_meta, "current_page")
            last_page = _meta_int(final_meta, "last_page")
            if current_page >= last_page or not page_data:
                break
            if current_page < page:
                raise WhitesparkResponseError(
                    "Whitespark pagination meta.current_page moved backwards."
                )
            page = current_page + 1

        total = final_meta.get("total")
        return {
            "data": grids,
            "meta": {
                "returned": len(grids),
                "total": total,
                "pages_fetched": pages_fetched,
                "last_page": final_meta.get("last_page"),
                "requested_limit": limit,
                "truncated": isinstance(total, int) and len(grids) < total,
                "final_page_meta": final_meta,
            },
        }

    async def get_latest_screenshot(self, *, grid_id: str, mode: str) -> dict[str, Any]:
        """Return bytes and response headers from the documented screenshot route."""
        if mode not in {"with-metrics", "without-metrics"}:
            raise ValueError("mode must be 'with-metrics' or 'without-metrics'")
        response = await self._request(
            "GET",
            f"/grid/{path_id(grid_id, 'grid_id')}/download-screenshot/{mode}",
            headers={"Accept": "*/*"},
        )
        return {
            "content": response.content,
            "content_type": response.headers.get("content-type"),
            "content_disposition": response.headers.get("content-disposition"),
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        request_headers = {
            "Authorization": f"Bearer {self._config.api_token}",
            "Accept": "application/json",
            **(headers or {}),
        }
        url = f"{self._config.api_base_url}{path}"
        for attempt in range(self._config.max_retries + 1):
            response = await self._http_client.request(
                method,
                url,
                params=params,
                headers=request_headers,
                follow_redirects=False,
            )
            if response.status_code != 429 and not 500 <= response.status_code <= 599:
                break
            if attempt == self._config.max_retries:
                break
            await self._sleep(_retry_delay(response, attempt, self._config.retry_backoff_seconds))

        if response.is_redirect or response.is_error:
            raise WhitesparkAPIError(response.status_code, _vendor_error_message(response))
        return response


def _meta_int(meta: Mapping[str, Any], key: str) -> int:
    value = meta.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WhitesparkResponseError(
            f"Whitespark GET /grids meta.{key} must be a positive integer."
        )
    return value


def _retry_delay(response: httpx.Response, attempt: int, base_delay: float) -> float:
    retry_after = response.headers.get("retry-after", "").strip()
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 60.0))
        except ValueError:
            pass
    return min(base_delay * (2**attempt), 60.0)


def _vendor_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or response.reason_phrase or "Unknown error"

    if isinstance(payload, dict):
        for key in ("message", "error", "detail"):
            if key in payload:
                value = payload[key]
                if isinstance(value, str):
                    return value
                if isinstance(value, dict):
                    nested = value.get("message") or value.get("detail")
                    if isinstance(nested, str):
                        return nested
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
