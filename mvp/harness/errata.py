"""The 249-cell errata list (D-05-20) -- a re-run of 04-REVIEW-FIX.md's
own WR-02 measurement, not a new computation designed from scratch.
Recompute every catalogued label IN MEMORY with the CURRENT (fixed,
`null_stale`) rule against the same curated inputs (day D and D+1)
`features.build.build_features_day` reads, and diff against the COMMITTED
partition already on the lake -- keeping only the cells that disagree.

NO WRITE, ANYWHERE. `features.api.for_build` (the one sanctioned kernel
entry point, `tools.check_single_feature_path`) runs in memory only;
`compute_errata_cells` never calls `write_feature_partition` or
`issue_feature_manifest`, and never touches `lake/features/` or
`mvp/data/lake_registry/manifests/BTCUSDT.features/` (T-05-09). The real,
git-committed `mvp/data/lake_registry/errata/<id>.json` registry entry is
05-07-PLAN.md's job, landing in the same commit as the
`check_manifest_append_only` guardrail extension -- this module computes
and proves the list, it does not commit it.

ONLY THE THREE ORIGINAL DAYS (2026-09-12/13/14) CAN HAVE ERRATA. The four
days added in 05-00-PLAN.md (2026-09-15..18) were built AFTER the
`null_stale` fix already landed (`spec/labels.toml`'s own note: the rule
was added 2026-09-19), so they carry no fabricated zeros by construction
-- calling `compute_errata_cells` on any of them is meaningless (there is
nothing for the fixed rule to disagree with itself about) and is not done
anywhere in this plan.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from data import store
from data.dq.checks import load_dq_thresholds
from data.store import FEATURES_TIER, by_date_index_path, compute_manifest_id
from features.api import for_build
from features.event_stream import assert_strict_total_order, merge_curated_streams
from features.labels import (
    append_next_day_quotes,
    compute_labels,
    next_day_quote_series,
)
from features.tier import LABEL_COLUMNS

__all__ = [
    "ErrataManifestError",
    "compute_errata_cells",
    "errata_manifest_path",
    "mask_errata_cells",
    "read_errata_manifest",
]

_L1_STREAM = "bookTicker"
_TRADE_STREAM = "trade"


class ErrataManifestError(ValueError):
    """Raised by `read_errata_manifest` for an errata-manifest protocol
    violation: named-but-missing, tampered (self-hash mismatch), or a
    `symbol`/`version` that disagrees with the segment manifest naming it.

    A dedicated class (not a bare `ValueError`, unlike this module's other
    raises) so `harness.accessor.materialize`'s fail-closed contract can be
    asserted against precisely -- a bare `ValueError` would also match
    `_find_entry`'s or `read_segment_manifest`'s own unrelated raises,
    silently passing a test for the wrong reason."""


def _load_curated(
    symbol: str, stream: str, date: str, *, registry_root: Path, lake_root: Path
) -> pl.DataFrame:
    """Day `date`'s curated `stream` partition, through the by-date
    pointer and `data.store.load_curated` -- the same public path
    `features.build._load_curated_day` uses internally, called here
    directly (not that private function) so this module's own provenance
    is independently readable."""
    dataset = f"{symbol}.{stream}"
    idx = by_date_index_path(Path(registry_root), dataset, symbol, stream, date)
    manifest_id = json.loads(idx.read_text())["manifest_id"]
    return store.load_curated(
        manifest_id,
        dataset,
        registry_root=Path(registry_root),
        lake_root=Path(lake_root),
    )


def _committed_label_columns(
    symbol: str, date: str, *, registry_root: Path, lake_root: Path
) -> pl.DataFrame:
    """`(etime, decision_seq, *LABEL_COLUMNS)` from the COMMITTED, already
    written features partition for `date` -- `pl.scan_parquet` on the
    manifest's own verified partition path, never a bare glob."""
    dataset = f"{symbol}.{FEATURES_TIER}"
    idx = by_date_index_path(Path(registry_root), dataset, symbol, FEATURES_TIER, date)
    manifest_id = json.loads(idx.read_text())["manifest_id"]
    manifest = store.resolve_manifest(
        manifest_id,
        dataset,
        registry_root=Path(registry_root),
        lake_root=Path(lake_root),
        expected_tier=FEATURES_TIER,
    )
    partition_path = Path(lake_root) / manifest["partitions"][0]["path"]
    return (
        pl.scan_parquet(partition_path)
        .select("etime", "decision_seq", *LABEL_COLUMNS)
        .collect()
    )


def compute_errata_cells(
    symbol: str,
    dates: list[str],
    *,
    registry_root: Path,
    lake_root: Path,
) -> list[dict]:
    """Every `(date, etime, decision_seq, label_column)` cell where the
    in-memory recompute (current, fixed rule) disagrees with the
    committed partition's stored value -- re-running 04-REVIEW-FIX.md's
    WR-02 measurement, per date in `dates`.

    Read-only. No partition, manifest or DQ report is written.

    STOPS LOUDLY (raises `ValueError`), rather than silently including a
    row, on any disagreement that is not the expected shape -- the
    committed value finite and exactly `0.0`, the recompute `NaN`
    (`null_stale` firing where the old, unbounded-carry rule produced a
    fabricated zero). A finite-vs-finite mismatch, or a disagreement in
    the OTHER direction (recompute defined, committed null), is not a
    known errata shape and must not be silently masked as one.
    """
    registry_root = Path(registry_root)
    lake_root = Path(lake_root)
    thresholds = load_dq_thresholds()
    cells: list[dict] = []

    for date in dates:
        l1_df = _load_curated(
            symbol, _L1_STREAM, date, registry_root=registry_root, lake_root=lake_root
        )
        trade_df = _load_curated(
            symbol,
            _TRADE_STREAM,
            date,
            registry_root=registry_root,
            lake_root=lake_root,
        )
        merged, _merge_stats = merge_curated_streams(l1_df, trade_df)
        del l1_df, trade_df
        assert_strict_total_order(merged)

        built = for_build(merged)
        del merged

        next_etime, next_mid = next_day_quote_series(
            symbol, date, registry_root=registry_root, lake_root=lake_root
        )
        quote_etime, quote_mid = append_next_day_quotes(
            built.quote_etime, built.quote_mid, next_etime, next_mid
        )
        del next_etime, next_mid

        decision_etime = built.frame["etime"].to_numpy()
        decision_mid = built.frame["mid"].to_numpy()
        decision_seq = built.frame["decision_seq"].to_numpy()

        labels, _label_stats = compute_labels(
            decision_etime,
            decision_mid,
            quote_etime,
            quote_mid,
            gap_threshold_ns=thresholds.label_gap.max_quote_gap_ns,
        )
        del quote_etime, quote_mid, built

        recompute = pl.DataFrame(
            {
                "etime": pl.Series(decision_etime, dtype=pl.Int64),
                "decision_seq": pl.Series(decision_seq, dtype=pl.Int64),
                **{
                    # The one NaN -> null conversion point for this
                    # module, mirroring `features.tier._nan_to_null`'s own
                    # single site -- `compute_labels` returns NaN for "no
                    # label", and the committed partition stores null, so
                    # the diff below must compare like with like.
                    name: pl.Series(labels[name], dtype=pl.Float64).fill_nan(None)
                    for name in LABEL_COLUMNS
                },
            }
        )
        del decision_etime, decision_mid, decision_seq, labels

        committed = _committed_label_columns(
            symbol, date, registry_root=registry_root, lake_root=lake_root
        )

        joined = recompute.join(
            committed, on=("etime", "decision_seq"), suffix="_committed", how="inner"
        )
        del recompute, committed
        if joined.height == 0:
            raise ValueError(
                f"compute_errata_cells: {date} join on (etime, decision_seq) "
                "produced zero rows -- the recompute and the committed "
                "partition do not share a single decision row"
            )

        for label_column in LABEL_COLUMNS:
            recomputed_col = pl.col(label_column)
            committed_col = pl.col(f"{label_column}_committed")
            disagree = joined.filter(
                (recomputed_col.is_null() != committed_col.is_null())
                | (
                    recomputed_col.is_not_null()
                    & committed_col.is_not_null()
                    & (recomputed_col != committed_col)
                )
            )
            if disagree.height == 0:
                continue

            not_the_known_shape = disagree.filter(
                recomputed_col.is_not_null() | (committed_col != 0.0)
            )
            if not_the_known_shape.height:
                raise ValueError(
                    f"compute_errata_cells: {date}/{label_column} has "
                    f"{not_the_known_shape.height} disagreement(s) that are "
                    "NOT the known errata shape (committed==0.0, "
                    "recompute==null) -- refusing to include them; see "
                    f"{not_the_known_shape.select('etime', 'decision_seq', label_column, f'{label_column}_committed').head(5)}"
                )

            for etime, decision_seq_val in zip(
                disagree["etime"].to_list(), disagree["decision_seq"].to_list()
            ):
                cells.append(
                    {
                        "date": date,
                        "etime": int(etime),
                        "decision_seq": int(decision_seq_val),
                        "label_column": label_column,
                    }
                )
        del joined

    return cells


def mask_errata_cells(df: pl.DataFrame, cells: list[dict]) -> pl.DataFrame:
    """Null out exactly the `(etime, decision_seq, label_column)` cells
    named in `cells`, leaving every other cell in `df` unchanged (D-05-20).

    `cells`' `date` key, if present, is not a join key here -- the caller
    (`harness.accessor.materialize`) has already scoped `df` to one
    segment's rows; the errata list carries `date` for readability outside
    this function, not as a second identity component `mask_errata_cells`
    needs. The join key is `(etime, decision_seq)` -- narrowing it to
    `etime` alone would null out every row sharing an `etime` with an
    errata cell, which is wrong the moment two decision rows share one
    `etime` (the ordinary multi-event-per-millisecond case).
    """
    if not cells:
        return df

    by_label: dict[str, list[tuple[int, int]]] = {}
    for cell in cells:
        by_label.setdefault(cell["label_column"], []).append(
            (int(cell["etime"]), int(cell["decision_seq"]))
        )

    out = df
    for label_column, pairs in by_label.items():
        if label_column not in out.columns:
            continue
        hit = pl.DataFrame(
            {
                "etime": pl.Series([p[0] for p in pairs], dtype=pl.Int64),
                "decision_seq": pl.Series([p[1] for p in pairs], dtype=pl.Int64),
                "_errata_hit": [True] * len(pairs),
            }
        )
        out = out.join(hit, on=("etime", "decision_seq"), how="left")
        out = out.with_columns(
            pl.when(pl.col("_errata_hit").is_not_null())
            .then(None)
            .otherwise(pl.col(label_column))
            .alias(label_column)
        ).drop("_errata_hit")
    return out


def errata_manifest_path(registry_root: Path, errata_id: str) -> Path:
    """Single source of truth for an errata manifest JSON's on-disk path --
    `registry_root/errata/<errata_id>.json`, flat, mirroring
    `harness.segments.segment_manifest_path`'s own shape (D-05-07: a
    sibling content-addressed registry, same rule)."""
    return Path(registry_root) / "errata" / f"{errata_id}.json"


def read_errata_manifest(
    registry_root: Path,
    errata_id: str,
    *,
    symbol: str,
    version: int,
) -> list[dict]:
    """Return the `cells` a segment manifest's own `errata_id` names --
    05-VERIFICATION-FIX.md Gap 2: the read-time resolution D-05-20 and
    spec.md's "Fold harness" section both promise ("the harness masks
    exactly those cells to null at read time") but `harness.accessor.
    materialize` never performed until this fix.

    Mirrors `harness.segments.read_segment_manifest`'s self-hash-on-read
    shape, scaled up with two more checks an errata manifest's caller
    needs that a segment manifest's caller does not (it is resolved BY a
    segment manifest, not the other way around):

    1. The file must exist. A named-but-missing errata manifest FAILS
       CLOSED (raises `ErrataManifestError`) -- it never silently degrades
       to "mask nothing", the exact failure direction `data.holdout`'s own
       fail-closed doctrine forbids (an unreadable registry entry must
       never read as "nothing to apply").
    2. The body's self-hash is re-verified `compute_manifest_id(manifest)
       == errata_id` -- against BOTH the value this function was called
       with (which the caller derived from the segment manifest's own
       `errata_id` field, i.e. effectively the filename stem) AND the
       body's own `manifest_id` field, so a hand-edited body whose
       `manifest_id` field was "helpfully" kept in sync with a corrupted
       `cells` list is caught exactly as a body left honestly stale is.
    3. `symbol`/`version` must match the caller's segment manifest exactly
       -- an errata list computed for a different symbol or catalogue
       version names cells that mean nothing against this read.

    Any of the three raises `ErrataManifestError` -- a dedicated class, so
    the caller's fail-closed contract can be asserted against precisely,
    never conflated with `_find_entry`'s or `read_segment_manifest`'s own
    unrelated `ValueError`s.

    `errata_id=None` is NOT a valid call here -- the caller
    (`harness.accessor.materialize`) is the one that decides "no errata
    applies" and skips calling this function entirely; `errata_id: null`
    in the segment manifest is the ONLY way to mask nothing (never an
    empty-but-present errata id reaching this far).
    """
    registry_root = Path(registry_root)
    path = errata_manifest_path(registry_root, errata_id)
    if not path.exists():
        raise ErrataManifestError(
            f"errata manifest {errata_id!r}, named by the segment manifest, "
            f"is missing at {path} -- refusing to mask nothing when an "
            "errata id was explicitly named (fail-closed, D-05-20)"
        )
    manifest = json.loads(path.read_text())
    body_manifest_id = manifest.get("manifest_id")
    recomputed = compute_manifest_id(manifest)
    if recomputed != errata_id or body_manifest_id != errata_id:
        raise ErrataManifestError(
            f"{path}: errata manifest hash mismatch -- named id {errata_id}, "
            f"body's own manifest_id field {body_manifest_id!r}, recomputed "
            f"{recomputed} from its own body -- refusing to trust a "
            "tampered or mismatched errata manifest"
        )
    if manifest.get("symbol") != symbol:
        raise ErrataManifestError(
            f"{path}: errata manifest symbol {manifest.get('symbol')!r} "
            f"does not match the segment manifest's own symbol {symbol!r}"
        )
    if manifest.get("version") != version:
        raise ErrataManifestError(
            f"{path}: errata manifest version {manifest.get('version')!r} "
            f"does not match the segment manifest's own version {version!r}"
        )
    return manifest["cells"]
