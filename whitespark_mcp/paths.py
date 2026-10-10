"""Validate resource identifiers before composing URL paths."""

import re
from typing import Any
from urllib.parse import quote
from uuid import UUID

from mcp.server.mcpserver.exceptions import ToolError

_PLAIN_ID = re.compile(r"[A-Za-z0-9_-]+")


def path_id(value: str | int | UUID, parameter: str) -> str:
    """Accept one plain identifier, then encode it as a single URL segment."""
    if (
        isinstance(value, bool)
        or not isinstance(value, str | int | UUID)
        or not _PLAIN_ID.fullmatch(str(value))
    ):
        raise ToolError(
            f"{parameter} must be a non-empty plain identifier using only ASCII letters, "
            "digits, underscores, or hyphens. Supply the record ID, not a URL or encoded path."
        )
    return quote(str(value), safe="")


_TOOL_PATH_IDS: dict[str, tuple[str, ...]] = {
    "get_latest_grid_screenshot": ("grid_id",),
    "get_latest_grid_screenshot_without_metrics": ("grid_id",),
    "batch_get_latest_grid_screenshots": ("grid_ids",),
}


def validate_path_arguments(name: str, arguments: dict[str, Any]) -> None:
    """Keep actionable ID errors ahead of SDK coercion, without changing schemas."""
    for parameter in _TOOL_PATH_IDS.get(name, ()):
        if parameter not in arguments:
            continue  # The SDK reports missing required arguments.
        value = arguments[parameter]
        if parameter == "grid_ids" and isinstance(value, list):
            for item in value:
                path_id(item, parameter)
        else:
            path_id(value, parameter)
