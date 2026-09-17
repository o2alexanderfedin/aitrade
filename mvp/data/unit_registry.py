"""Typed registry over the archive unit-convention TOML source of truth.

`mvp/spec/unit_registry.toml` is hand-edited and parsed here with stdlib
`tomllib` -- mirrors `spec/catalogue.py`'s TOML-registry + frozen-dataclass
pattern exactly (see that module's docstring for the rationale). Downloader
and normalize code must reach a (market, dataset) unit convention only
through `get_unit_entry`, never by importing the TOML directly or hardcoding
a `header=`/`time_unit=` literal inline -- that indirection is what makes an
unrecognized (market, dataset) pair a mechanical `UnitRegistryError` instead
of a silently-guessed default.

No module-level caching, matching `catalogue.py`: the registry is a small
hand-edited file; every `get_unit_entry` call re-reads from disk.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent

UNIT_REGISTRY_TOML = PKG_ROOT.parent / "spec" / "unit_registry.toml"

REQUIRED_ENTRY_KEYS: frozenset[str] = frozenset(
    {"header", "time_unit", "columns", "notes"}
)


class UnitRegistryError(ValueError):
    """Raised on an unknown (market, dataset) pair or a malformed registry entry."""


@dataclass(frozen=True)
class UnitRegistryEntry:
    market: str
    dataset: str
    header: bool
    time_unit: str
    columns: list[str]
    notes: str


def _load_toml(path: Path) -> dict[str, dict]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def get_unit_entry(
    market: str, dataset: str, path: Path = UNIT_REGISTRY_TOML
) -> UnitRegistryEntry:
    """Return the unit-convention entry for `(market, dataset)`.

    Raises `UnitRegistryError` on an unknown (market, dataset) pair -- never
    returns a guessed/default entry -- or on a registry row missing one of
    `REQUIRED_ENTRY_KEYS`.
    """
    raw = _load_toml(path)
    key = f"{market}.{dataset}"
    try:
        entry = raw[key]
    except KeyError:
        raise UnitRegistryError(
            f"unknown (market, dataset) pair: ({market!r}, {dataset!r})"
        ) from None

    missing = REQUIRED_ENTRY_KEYS - entry.keys()
    if missing:
        raise UnitRegistryError(
            f"unit registry entry {key!r} missing required key(s): {sorted(missing)}"
        )

    return UnitRegistryEntry(
        market=market,
        dataset=dataset,
        header=entry["header"],
        time_unit=entry["time_unit"],
        columns=entry["columns"],
        notes=entry["notes"],
    )
