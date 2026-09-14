"""Typed registry over the feature/label catalogue TOML source of truth.

`mvp/spec/features.toml` and `mvp/spec/labels.toml` are hand-edited by humans and
parsed here with stdlib `tomllib` -- no project code execution required to validate
them (see `mvp/spec.md`'s Feature/Label catalogue sections). Training/inference code
must reach a feature or label only through `get_feature`/`get_label`, never by
importing the TOML directly -- that is what makes "uncatalogued feature in training
code" mechanically detectable (Plan 02).

No module-level caching: catalogues are small hand-edited files; every `get_feature`/
`get_label` call re-reads from disk, trading a few microseconds for zero staleness
risk across a long-running process that might otherwise miss a hot-reloaded edit.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent

FEATURES_TOML = PKG_ROOT / "features.toml"
LABELS_TOML = PKG_ROOT / "labels.toml"

REQUIRED_FEATURE_KEYS: frozenset[str] = frozenset(
    {
        "definition",
        "information_set",
        "lag",
        "normalization",
        "source_datasets",
        "notes",
        "version",
        "introduced",
    }
)

REQUIRED_LABEL_KEYS: frozenset[str] = frozenset(
    {
        "horizon",
        "computation",
        "information_set",
        "embargo",
        "notes",
        "version",
        "introduced",
    }
)


class CatalogueError(ValueError):
    """Raised on a malformed catalogue entry or an unknown feature/label name."""


@dataclass(frozen=True)
class FeatureEntry:
    name: str
    definition: str
    information_set: str
    lag: int
    normalization: str
    source_datasets: list[str]
    notes: str
    version: int
    introduced: str


@dataclass(frozen=True)
class LabelEntry:
    name: str
    horizon: str
    computation: str
    information_set: str
    embargo: str
    notes: str
    version: int
    introduced: str


def _load_toml(path: Path) -> dict[str, dict]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_features(path: Path = FEATURES_TOML) -> dict[str, FeatureEntry]:
    """Load and validate `features.toml`; raise CatalogueError on a missing key."""
    raw = _load_toml(path)
    entries: dict[str, FeatureEntry] = {}
    for name, entry in raw.items():
        missing = REQUIRED_FEATURE_KEYS - entry.keys()
        if missing:
            raise CatalogueError(
                f"feature {name!r} missing required key(s): {sorted(missing)}"
            )
        entries[name] = FeatureEntry(name=name, **{k: entry[k] for k in REQUIRED_FEATURE_KEYS})
    return entries


def load_labels(path: Path = LABELS_TOML) -> dict[str, LabelEntry]:
    """Load and validate `labels.toml`; raise CatalogueError on a missing key."""
    raw = _load_toml(path)
    entries: dict[str, LabelEntry] = {}
    for name, entry in raw.items():
        missing = REQUIRED_LABEL_KEYS - entry.keys()
        if missing:
            raise CatalogueError(
                f"label {name!r} missing required key(s): {sorted(missing)}"
            )
        entries[name] = LabelEntry(name=name, **{k: entry[k] for k in REQUIRED_LABEL_KEYS})
    return entries


def get_feature(name: str, path: Path = FEATURES_TOML) -> FeatureEntry:
    """Return the named feature entry; raise CatalogueError if it does not exist."""
    try:
        return load_features(path)[name]
    except KeyError:
        raise CatalogueError(f"unknown feature {name!r}") from None


def get_label(name: str, path: Path = LABELS_TOML) -> LabelEntry:
    """Return the named label entry; raise CatalogueError if it does not exist."""
    try:
        return load_labels(path)[name]
    except KeyError:
        raise CatalogueError(f"unknown label {name!r}") from None


def diff_definition_changes(
    old: dict[str, dict], new: dict[str, dict], field: str = "definition"
) -> list[str]:
    """Return sorted names present in both `old` and `new` whose `field` differs.

    A name only in `new` (brand-new entry) is never flagged. A name whose `field`
    value is byte-identical across both is never flagged. `field` defaults to
    "definition" (the features' comparison field); callers comparing label dicts
    MUST pass `field="computation"` explicitly -- label entries have no `definition`
    key (see REQUIRED_LABEL_KEYS), so using the default against label dicts raises
    KeyError by design rather than silently comparing the wrong thing.
    """
    changed = [
        name
        for name in sorted(old.keys() & new.keys())
        if old[name][field] != new[name][field]
    ]
    return changed
