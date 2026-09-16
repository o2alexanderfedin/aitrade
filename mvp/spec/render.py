"""Regenerate mvp/spec.md's marker-delimited catalogue tables from the TOML source
of truth (mvp/spec/{features,labels,dq_thresholds}.toml).

Invoked as `uv run --directory mvp python -m spec.render` (process cwd = mvp/) or
directly as `./.venv/bin/python3 -m spec.render` from within mvp/. Never hardcode a
relative "mvp/spec.md" path -- from cwd=mvp/ that resolves to the nonexistent
mvp/mvp/spec.md. PKG_ROOT anchors every path instead.

Marker search anchors on exact-line equality (`line.strip() == MARKER`), never a
substring `str.replace` -- a marker string appearing inside a table cell must not be
mistaken for the real marker line.

DQ-thresholds marker pair (Plan 04): unlike the features/labels markers, which are
ALWAYS present in the real spec.md and rendered on every `render_spec` call,
`dq_thresholds` is an OPTIONAL parameter -- pre-existing hermetic
`tests/spec/test_render.py` fixtures build a small in-memory `spec_md_text` with
only the features/labels markers, and `_replace_block` raises `ValueError` if asked
to replace a marker pair that isn't present. Passing `dq_thresholds=None` (the
default) skips that third block entirely, so the pre-existing feature/label-only
call shape keeps working unchanged; `tools/check_spec_diff.py`'s real-spec.md call
always passes the loaded TOML dict.
"""

from __future__ import annotations

from pathlib import Path

from spec.catalogue import (
    FeatureEntry,
    LabelEntry,
    _load_toml,
    load_features,
    load_labels,
)

PKG_ROOT = Path(__file__).resolve().parents[1]

SPEC_MD = PKG_ROOT / "spec.md"
DQ_THRESHOLDS_TOML = PKG_ROOT / "spec" / "dq_thresholds.toml"

FEATURES_BEGIN = "<!-- catalogue:features:begin -->"
FEATURES_END = "<!-- catalogue:features:end -->"
LABELS_BEGIN = "<!-- catalogue:labels:begin -->"
LABELS_END = "<!-- catalogue:labels:end -->"
DQ_THRESHOLDS_BEGIN = "<!-- catalogue:dq_thresholds:begin -->"
DQ_THRESHOLDS_END = "<!-- catalogue:dq_thresholds:end -->"

# Header + separator rendered as part of the marker-delimited block (not returned by
# render_features_table/render_labels_table, which produce rows only) so that the
# complete replacement text between the markers is always a single, GFM-valid table --
# a marker line placed *inside* an existing table (e.g. between the separator row and
# the first data row) is an HTML-comment line, which GFM treats as a block-level
# structure that terminates table parsing (GFM sec 4.10), breaking rendering on
# GitHub. Wrapping the whole table (header + separator + rows) between the markers
# avoids that.
FEATURES_HEADER = (
    "| Name | Definition | Information set (latest input timestamp ≤ decision `t`) "
    "| Lag (if any) | Normalization | Source dataset(s) | Notes |\n"
    "| --- | --- | --- | --- | --- | --- | --- |"
)
LABELS_HEADER = (
    "| Name | Horizon | Computation | Information set required to evaluate "
    "| Embargo | Notes |\n"
    "| --- | --- | --- | --- | --- | --- |"
)
DQ_THRESHOLDS_HEADER = "| Check | Threshold(s) | Notes |\n| --- | --- | --- |"


def _escape_cell(value: str) -> str:
    """Escape a value for embedding in a single GFM table cell: `|` would
    otherwise terminate the cell early, and a literal newline (from a
    triple-quoted multi-line TOML string) would otherwise split one logical
    row across multiple physical lines, breaking the table."""
    return value.replace("|", "\\|").replace("\n", "<br>")


def render_features_table(entries: dict[str, FeatureEntry]) -> str:
    """Render feature rows (no header) in `entries`' declaration (dict) order."""
    rows = []
    for name, entry in entries.items():
        source_datasets = ", ".join(entry.source_datasets)
        rows.append(
            "| `{name}` | {definition} | {information_set} | {lag} | "
            "{normalization} | {source_datasets} | {notes} |".format(
                name=_escape_cell(name),
                definition=_escape_cell(entry.definition),
                information_set=_escape_cell(entry.information_set),
                lag=entry.lag,
                normalization=_escape_cell(entry.normalization),
                source_datasets=_escape_cell(source_datasets),
                notes=_escape_cell(entry.notes),
            )
        )
    return "\n".join(rows)


def render_labels_table(entries: dict[str, LabelEntry]) -> str:
    """Render label rows (no header) in `entries`' declaration (dict) order."""
    rows = []
    for name, entry in entries.items():
        rows.append(
            "| `{name}` | {horizon} | {computation} | {information_set} | "
            "{embargo} | {notes} |".format(
                name=_escape_cell(name),
                horizon=_escape_cell(entry.horizon),
                computation=_escape_cell(entry.computation),
                information_set=_escape_cell(entry.information_set),
                embargo=_escape_cell(entry.embargo),
                notes=_escape_cell(entry.notes),
            )
        )
    return "\n".join(rows)


def load_dq_thresholds_raw(path: Path = DQ_THRESHOLDS_TOML) -> dict[str, dict]:
    """Return the raw parsed `dq_thresholds.toml` dict (table name ->
    fields, including `notes`) -- unlike `data.dq.checks.load_dq_thresholds`,
    this is NOT wrapped in typed dataclasses, since `render_dq_thresholds_table`
    below only needs to iterate name/field pairs generically across six
    check tables with different field shapes."""
    return _load_toml(path)


def render_dq_thresholds_table(thresholds: dict[str, dict]) -> str:
    """Render one row per check/config table in `thresholds` (the raw
    parsed `dq_thresholds.toml` dict) in its declaration order -- same
    "declaration order, not sorted" convention as `render_features_table`.
    Every non-`notes` field is rendered as `key=value` in the Threshold(s)
    column; `notes` gets its own column (its embedded newlines are escaped
    to `<br>` by `_escape_cell`, same as a multi-line feature `notes` field).
    """
    rows = []
    for name, entry in thresholds.items():
        fields = ", ".join(f"{k}={v}" for k, v in entry.items() if k != "notes")
        notes = str(entry.get("notes", "")).strip()
        rows.append(
            "| `{name}` | {fields} | {notes} |".format(
                name=_escape_cell(name),
                fields=_escape_cell(fields),
                notes=_escape_cell(notes),
            )
        )
    return "\n".join(rows)


def _replace_block(
    lines: list[str],
    begin_marker: str,
    end_marker: str,
    header: str,
    rows: str,
) -> list[str]:
    begin_idx = next(
        (i for i, line in enumerate(lines) if line.strip() == begin_marker), None
    )
    end_idx = next(
        (
            i
            for i, line in enumerate(lines)
            if i > (begin_idx if begin_idx is not None else -1)
            and line.strip() == end_marker
        ),
        None,
    )
    if begin_idx is None or end_idx is None:
        raise ValueError(
            f"marker pair {begin_marker!r}/{end_marker!r} not found or unbalanced "
            "-- refusing to silently no-op"
        )
    replacement = [begin_marker, *header.split("\n"), *rows.split("\n"), end_marker]
    return lines[:begin_idx] + replacement + lines[end_idx + 1 :]


def render_spec(
    spec_md_text: str,
    features: dict[str, FeatureEntry],
    labels: dict[str, LabelEntry],
    dq_thresholds: dict[str, dict] | None = None,
) -> str:
    """Return `spec_md_text` with only the text between the exact-line marker pairs
    replaced by the freshly rendered catalogue tables. Content outside the markers is
    returned byte-identical. Idempotent: calling this twice on its own output produces
    byte-identical text both times.

    `dq_thresholds` defaults to `None`, which skips the DQ-thresholds marker block
    entirely (see module docstring) -- pass the raw `dq_thresholds.toml` dict
    (`load_dq_thresholds_raw()`) to also render that third block.
    """
    lines = spec_md_text.split("\n")
    lines = _replace_block(
        lines,
        FEATURES_BEGIN,
        FEATURES_END,
        FEATURES_HEADER,
        render_features_table(features),
    )
    lines = _replace_block(
        lines, LABELS_BEGIN, LABELS_END, LABELS_HEADER, render_labels_table(labels)
    )
    if dq_thresholds is not None:
        lines = _replace_block(
            lines,
            DQ_THRESHOLDS_BEGIN,
            DQ_THRESHOLDS_END,
            DQ_THRESHOLDS_HEADER,
            render_dq_thresholds_table(dq_thresholds),
        )
    return "\n".join(lines)


def main() -> int:
    features = load_features()
    labels = load_labels()
    dq_thresholds = load_dq_thresholds_raw()
    current = SPEC_MD.read_text()
    rendered = render_spec(current, features, labels, dq_thresholds)
    SPEC_MD.write_text(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
