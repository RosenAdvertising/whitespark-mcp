"""Shared pytest configuration."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Run asynchronous tests on asyncio only."""
    return "asyncio"
