"""data_root validation guard.

Refuses to run the capture daemon against a data_root that does not exist,
is not writable, is under a CloudStorage/OneDrive-synced path, or has less
than the configured minimum free space. This makes the mistake impossible
rather than merely documented (see 01-CONTEXT.md).
"""

from __future__ import annotations

import pathlib
import shutil


DEFAULT_MIN_FREE_GB = 50.0


class DataRootError(ValueError):
    """Raised when a data_root fails validation."""


def validate_data_root(
    data_root: str, min_free_gb: float = DEFAULT_MIN_FREE_GB
) -> pathlib.Path:
    """Validate `data_root` and return its resolved Path.

    Checks, in order:
    1. `data_root` is required — no default exists.
    2. The resolved path must not be under a CloudStorage/OneDrive-synced
       directory (checked before existence, so a nonexistent path under
       that marker still raises the cloud-path error).
    3. The path must exist.
    4. The path must be a directory.
    5. The path must be writable (probed by touch + unlink).
    6. The path must have at least `min_free_gb` GiB free.
    """
    if not data_root:
        raise DataRootError(
            "data_root is required and has no default — pass --data-root "
            "explicitly (see 01-CONTEXT.md)"
        )

    resolved = pathlib.Path(data_root).resolve()
    resolved_str = str(resolved)
    if "CloudStorage" in resolved_str:
        raise DataRootError(
            f"data_root {resolved_str!r} is under a CloudStorage-synced "
            "path — refusing to write capture data there"
        )
    if "OneDrive" in resolved_str:
        raise DataRootError(
            f"data_root {resolved_str!r} is under a OneDrive-synced "
            "path — refusing to write capture data there"
        )

    if not resolved.exists():
        raise DataRootError(f"data_root {resolved_str!r} does not exist")

    if not resolved.is_dir():
        raise DataRootError(f"data_root {resolved_str!r} is not a directory")

    probe = resolved / ".capture_write_probe"
    try:
        probe.touch()
        probe.unlink()
    except OSError as exc:
        raise DataRootError(
            f"data_root {resolved_str!r} is not writable: {exc}"
        ) from exc

    free_bytes = shutil.disk_usage(resolved).free
    free_gib = free_bytes / (1024**3)
    if free_gib < min_free_gb:
        raise DataRootError(
            f"data_root {resolved_str!r} has only {free_gib:.2f} GiB free, "
            f"below the required {min_free_gb:.2f} GiB free"
        )

    return resolved
