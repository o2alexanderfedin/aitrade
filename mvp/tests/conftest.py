"""Shared pytest fixtures for the mvp test suite.

This file exists so `mvp/tests` is a discoverable rootdir-relative package
per the `pythonpath = ["."]` config set in `mvp/pyproject.toml` (Plan 01
Task 1). Plan 02 adds the fake websocket server fixture below, reused by
Plan 03's reconnect/dedup tests unchanged in shape.

NUMBA_CACHE_DIR (Phase 4 Plan 01): pinned at the TOP of this file, before any
import that could reach numba. `@njit(cache=True)` with the variable unset
writes its `*.nbi`/`*.nbc` index/object files into the `__pycache__`
directory NEXT TO THE DEFINING SOURCE FILE (measured, 04-RESEARCH-NOTES.md
Q3) -- i.e. into `mvp/features/__pycache__/` on every pre-commit run.
`setdefault`, not assignment, so a developer or CI job that already pinned
its own cache dir keeps it. `tests/features/test_time_ns.py` asserts both
that the variable is set outside the package tree and that no cache artifact
has landed under `mvp/`.
"""

import os
import tempfile
from pathlib import Path

os.environ.setdefault(
    "NUMBA_CACHE_DIR", str(Path(tempfile.gettempdir()) / "aihf-numba-cache")
)

import pytest  # noqa: E402

from tests.fixtures.fake_ws_server import scripted_server as _scripted_server  # noqa: E402


@pytest.fixture
def scripted_server():
    """Return the `scripted_server` async-context-manager factory itself.

    No `pytest-asyncio`/`anyio` plugin is installed (see `uv.lock`), so
    this fixture cannot yield an already-entered async context manager —
    tests drive their own event loop via `asyncio.run()` and call
    `async with scripted_server(...) as port:` inside it.
    """
    return _scripted_server
