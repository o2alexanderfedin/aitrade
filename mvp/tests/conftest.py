"""Shared pytest fixtures for the mvp test suite.

This file exists so `mvp/tests` is a discoverable rootdir-relative package
per the `pythonpath = ["."]` config set in `mvp/pyproject.toml` (Plan 01
Task 1). Plan 02 adds the fake websocket server fixture below, reused by
Plan 03's reconnect/dedup tests unchanged in shape.
"""

import pytest

from tests.fixtures.fake_ws_server import scripted_server as _scripted_server


@pytest.fixture
def scripted_server():
    """Return the `scripted_server` async-context-manager factory itself.

    No `pytest-asyncio`/`anyio` plugin is installed (see `uv.lock`), so
    this fixture cannot yield an already-entered async context manager —
    tests drive their own event loop via `asyncio.run()` and call
    `async with scripted_server(...) as port:` inside it.
    """
    return _scripted_server
