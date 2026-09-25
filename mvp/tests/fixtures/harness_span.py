"""Real-span feature-tier fixtures for the harness's own tests (05-01-PLAN.md
Task 1) -- built through the REAL `features.tier.write_feature_partition`/
`issue_feature_manifest` writer path, against `tmp_path` lake/registry
roots, exactly as the rest of this codebase's tier tests do (05-RESEARCH.md
Q9). Etimes are PARAMETERIZED (`start_ns`, `step_ns`, `rows`), never
`tests/fixtures/feature_tier.py:feature_frame`'s 3-microsecond span -- that
span makes every purge/embargo invariant test pass vacuously against a
600-second-scale purge horizon (05-PATTERNS.md).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import polars as pl

from features.tier import (
    FEATURE_COLUMNS,
    FEATURE_ROW_SCHEMA,
    FEATURE_SCHEMA_VERSION,
    LABEL_COLUMNS,
    issue_feature_manifest,
    write_feature_partition,
)
from tests.fixtures.feature_build import write_dq_report

SYMBOL = "BTCUSDT"


def build_span_partition(
    lake_root: Path,
    registry_root: Path,
    *,
    symbol: str = SYMBOL,
    date: str,
    start_ns: int,
    step_ns: int,
    rows: int,
    decision_source_ranks: Sequence[int] | None = None,
    code_hash: str = "deadbeef",
) -> dict:
    """Write one real, `FEATURE_ROW_SCHEMA`-shaped features-tier partition
    spanning `rows` decision rows, `step_ns` apart, starting at `start_ns`,
    through the real writer/manifest-issuer pair, against `lake_root`/
    `registry_root` (both `tmp_path`-derived in every caller). Returns
    `{"manifest_id", "dataset", "symbol", "date", "etime_min", "etime_max",
    "rows"}`.

    Every feature/label column is a plain, non-null float (its VALUE is
    irrelevant to every purge/embargo/accessor test this fixture serves --
    only `etime` and the bookkeeping columns matter); `decision_source_rank`
    defaults to 0 (quote) on every row, so no row is ever "undefined age" for a
    later row-admission test.

    `decision_source_ranks` OVERRIDES that per row, and exists so a test can
    make `row_admission.stale_book_age_ns` produce something other than a column
    of zeros (07-02-PLAN.md Task 1): an all-quote span gives every row age 0, so
    both the stale and the undefined admission counts come out 0 and any test
    comparing them is comparing two zeros. A pattern with leading trade rows and
    a quote every nth row afterwards yields real `excluded_undefined` and
    `excluded_stale` counts, and -- because `stale_book_age_ns` forward-fills --
    counts that depend on row ORDER, not only on row count. Must be `rows` long;
    `None` reproduces the all-quote column exactly.
    """
    etimes = [start_ns + i * step_ns for i in range(rows)]
    if decision_source_ranks is None:
        ranks = [0] * rows
    elif len(decision_source_ranks) != rows:
        raise ValueError(
            f"build_span_partition: decision_source_ranks has "
            f"{len(decision_source_ranks)} entries, expected rows={rows}"
        )
    else:
        ranks = list(decision_source_ranks)
    columns: dict[str, list] = {
        "etime": etimes,
        "decision_source_rank": ranks,
        "decision_seq": list(range(rows)),
        "bid_price": [70_000.0 + float(i % 97) for i in range(rows)],
        "ask_price": [70_000.1 + float(i % 97) for i in range(rows)],
    }
    for name in FEATURE_COLUMNS:
        columns[name] = [float(i % 97) for i in range(rows)]
    for name in LABEL_COLUMNS:
        columns[name] = [0.0001 * (i % 97) for i in range(rows)]
    columns["warmup"] = [False] * rows
    columns["post_gap_warmup"] = [False] * rows
    columns["schema_version"] = [FEATURE_SCHEMA_VERSION] * rows

    df = pl.DataFrame(columns, schema=FEATURE_ROW_SCHEMA)

    partition_entry = write_feature_partition(
        df, lake_root=lake_root, symbol=symbol, date=date, registry_root=registry_root
    )
    manifest = issue_feature_manifest(
        symbol=symbol,
        date=date,
        partition_entry=partition_entry,
        curated_manifests=[],
        code_hash=code_hash,
        registry_root=registry_root,
    )
    # `features.tier.load_features` -> `store._enforce_dq_pause` requires an
    # "ok" (or acknowledged) DQ report row for THIS manifest before it will
    # return any rows at all -- without this, every real `load_features`
    # call through the harness accessor would raise `DQPauseError` on a
    # perfectly healthy synthetic fixture.
    write_dq_report(
        lake_root,
        date,
        [("features", manifest["manifest_id"], "ok")],
        symbol=symbol,
    )
    return {
        "manifest_id": manifest["manifest_id"],
        "dataset": manifest["dataset"],
        "symbol": symbol,
        "date": date,
        "etime_min": partition_entry["etime_min"],
        "etime_max": partition_entry["etime_max"],
        "rows": rows,
    }
