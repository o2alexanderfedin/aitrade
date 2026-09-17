"""RP-4: data.store.load_curated's DQ pause enforcement (DATA-07).

Three cases, all against tmp_path fixtures (synthetic, not the real lake):
1. A day marked "failed" with no acknowledgement raises; the matching
   acknowledgement makes it succeed; reverting the acknowledgement makes
   it raise again.
2. A date with NO report.parquet at all (never reported) raises
   (fail-closed on absence, not an implicit pass); a matching
   acknowledgement citing the missing report makes it succeed.
3. (Real-data case) is exercised directly against the live lake/registry
   in 03-04-SUMMARY.md's transcript, not here -- see that file for the
   real `load_curated` calls against all 111 real manifests.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import polars as pl
import pytest

from data.store import (
    DQPauseError,
    dq_acknowledgement_path,
    dq_report_path,
    issue_manifest,
    load_curated,
)

REPORT_SCHEMA = {
    "date": pl.Utf8,
    "symbol": pl.Utf8,
    "stream": pl.Utf8,
    "check": pl.Utf8,
    "dq_status": pl.Utf8,
    "value": pl.Float64,
    "count": pl.Int64,
    "detail": pl.Utf8,
}


def _write_partition(
    lake_root: Path, rel_path: str, date: str, df: pl.DataFrame
) -> dict:
    final_path = lake_root / rel_path
    final_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(final_path, compression="zstd")
    st = final_path.stat()
    return {
        "date": date,
        "path": rel_path,
        "sha256": hashlib.sha256(final_path.read_bytes()).hexdigest(),
        "rows": df.height,
        "size_bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "etime_min": int(df["etime"].min()),
        "etime_max": int(df["etime"].max()),
    }


def _write_report(
    lake_root: Path,
    symbol: str,
    stream: str,
    date: str,
    dq_status: str,
    manifest_id: str | None = None,
) -> None:
    """`manifest_id=None` writes the LEGACY (pre-WR-15) shape, with no
    `manifest_id` column."""
    path = dq_report_path(lake_root, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = {
        "date": [date],
        "symbol": [symbol],
        "stream": [stream],
        "check": ["gap_coverage"],
        "dq_status": [dq_status],
        "value": [9999.0 if dq_status == "failed" else 0.0],
        "count": [None],
        "detail": [None],
    }
    schema = dict(REPORT_SCHEMA)
    if manifest_id is not None:
        columns["manifest_id"] = [manifest_id]
        schema["manifest_id"] = pl.Utf8
    pl.DataFrame(columns, schema=schema).write_parquet(path, compression="zstd")


def _write_acknowledgement(
    registry_root: Path, symbol: str, stream: str, date: str, reason: str
) -> Path:
    path = dq_acknowledgement_path(registry_root, symbol, stream, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "date": date,
                "symbol": symbol,
                "stream": stream,
                "reason": reason,
                "who": "test",
                "when": "2026-09-16T00:00:00Z",
            }
        )
    )
    return path


def _issue_manifest(lake_root: Path, registry_root: Path, date: str) -> dict:
    df = pl.DataFrame({"trade_id": [1], "etime": [1_000], "price": [1.0]})
    part = _write_partition(lake_root, f"curated/part-{date}.parquet", date, df)
    return issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[part],
        code_hash="deadbeef",
        registry_root=registry_root,
    )


# --- Case 1: failed status, ack added, ack reverted ------------------------


def test_case1_failed_day_pauses_then_acknowledgement_unpauses_then_revert_repauses(
    tmp_path: Path,
):
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    date = "2026-09-12"
    manifest = _issue_manifest(lake_root, registry_root, date)
    _write_report(
        lake_root, "BTCUSDT", "trade", date, "failed", manifest["manifest_id"]
    )

    # RED: no acknowledgement -- raises.
    with pytest.raises(DQPauseError):
        load_curated(
            manifest["manifest_id"],
            "BTCUSDT.trade",
            registry_root=registry_root,
            lake_root=lake_root,
        )

    # GREEN: acknowledgement present -- succeeds.
    ack_path = _write_acknowledgement(
        registry_root, "BTCUSDT", "trade", date, "synthetic RP-4 case 1"
    )
    loaded = load_curated(
        manifest["manifest_id"],
        "BTCUSDT.trade",
        registry_root=registry_root,
        lake_root=lake_root,
    )
    assert loaded.height == 1

    # RED again: revert the acknowledgement -- raises again.
    ack_path.unlink()
    with pytest.raises(DQPauseError):
        load_curated(
            manifest["manifest_id"],
            "BTCUSDT.trade",
            registry_root=registry_root,
            lake_root=lake_root,
        )


# --- Case 2: no report.parquet at all (fail-closed on absence) -------------


def test_case2_missing_report_pauses_then_acknowledgement_citing_missing_report_unpauses(
    tmp_path: Path,
):
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    date = "2026-09-13"
    manifest = _issue_manifest(lake_root, registry_root, date)
    # Deliberately no dq_report_path(lake_root, date) written at all --
    # this (symbol, stream, date) has never been reported on.
    assert not dq_report_path(lake_root, date).exists()

    with pytest.raises(DQPauseError):
        load_curated(
            manifest["manifest_id"],
            "BTCUSDT.trade",
            registry_root=registry_root,
            lake_root=lake_root,
        )

    _write_acknowledgement(
        registry_root, "BTCUSDT", "trade", date, "no DQ report generated for this date"
    )
    loaded = load_curated(
        manifest["manifest_id"],
        "BTCUSDT.trade",
        registry_root=registry_root,
        lake_root=lake_root,
    )
    assert loaded.height == 1


def test_all_n_a_report_is_treated_as_missing_not_ok(tmp_path: Path):
    """A report DID run but every check reported n/a -- fail-closed, same
    as a genuinely missing report, never silently treated as a pass."""
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    date = "2026-09-14"
    manifest = _issue_manifest(lake_root, registry_root, date)
    _write_report(lake_root, "BTCUSDT", "trade", date, "n/a", manifest["manifest_id"])

    with pytest.raises(DQPauseError):
        load_curated(
            manifest["manifest_id"],
            "BTCUSDT.trade",
            registry_root=registry_root,
            lake_root=lake_root,
        )


def test_ok_status_needs_no_acknowledgement(tmp_path: Path):
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    date = "2026-09-15"
    manifest = _issue_manifest(lake_root, registry_root, date)
    _write_report(lake_root, "BTCUSDT", "trade", date, "ok", manifest["manifest_id"])

    loaded = load_curated(
        manifest["manifest_id"],
        "BTCUSDT.trade",
        registry_root=registry_root,
        lake_root=lake_root,
    )
    assert loaded.height == 1


# --- WR-01 (03-REVIEW.md): the acknowledgement's CONTENT is validated -------


@pytest.mark.parametrize(
    ("label", "content"),
    [
        ("zero-byte file", ""),
        ("not JSON", "ok\n"),
        ("JSON but not an object", "[]"),
        (
            "missing reason",
            json.dumps(
                {
                    "date": "2026-09-12",
                    "symbol": "BTCUSDT",
                    "stream": "trade",
                    "who": "alex",
                    "when": "2026-09-16T00:00:00Z",
                }
            ),
        ),
        (
            "blank reason",
            json.dumps(
                {
                    "date": "2026-09-12",
                    "symbol": "BTCUSDT",
                    "stream": "trade",
                    "reason": "   ",
                    "who": "alex",
                    "when": "2026-09-16T00:00:00Z",
                }
            ),
        ),
        (
            "empty who",
            json.dumps(
                {
                    "date": "2026-09-12",
                    "symbol": "BTCUSDT",
                    "stream": "trade",
                    "reason": "known outage",
                    "who": "",
                    "when": "2026-09-16T00:00:00Z",
                }
            ),
        ),
        (
            "unparseable when",
            json.dumps(
                {
                    "date": "2026-09-12",
                    "symbol": "BTCUSDT",
                    "stream": "trade",
                    "reason": "known outage",
                    "who": "alex",
                    "when": "yesterday",
                }
            ),
        ),
        (
            "copied from another date",
            json.dumps(
                {
                    "date": "2026-09-11",
                    "symbol": "BTCUSDT",
                    "stream": "trade",
                    "reason": "known outage",
                    "who": "alex",
                    "when": "2026-09-16T00:00:00Z",
                }
            ),
        ),
        (
            "copied from another stream",
            json.dumps(
                {
                    "date": "2026-09-12",
                    "symbol": "BTCUSDT",
                    "stream": "bookTicker",
                    "reason": "known outage",
                    "who": "alex",
                    "when": "2026-09-16T00:00:00Z",
                }
            ),
        ),
    ],
)
def test_invalid_acknowledgement_does_not_unpause_a_failed_day(
    tmp_path: Path, label: str, content: str
):
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    date = "2026-09-12"
    manifest = _issue_manifest(lake_root, registry_root, date)
    _write_report(
        lake_root, "BTCUSDT", "trade", date, "failed", manifest["manifest_id"]
    )
    ack = dq_acknowledgement_path(registry_root, "BTCUSDT", "trade", date)
    ack.parent.mkdir(parents=True, exist_ok=True)
    ack.write_text(content)

    with pytest.raises(DQPauseError, match="acknowledgement"):
        load_curated(
            manifest["manifest_id"],
            "BTCUSDT.trade",
            registry_root=registry_root,
            lake_root=lake_root,
        )


def test_acknowledgement_ids_are_reported_for_mlflow_tagging(tmp_path: Path):
    from data.store import dq_acknowledgement_ids

    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    date = "2026-09-12"
    manifest = _issue_manifest(lake_root, registry_root, date)
    _write_report(
        lake_root, "BTCUSDT", "trade", date, "failed", manifest["manifest_id"]
    )
    _write_acknowledgement(registry_root, "BTCUSDT", "trade", date, "known outage")

    assert dq_acknowledgement_ids(
        manifest, registry_root=registry_root, lake_root=lake_root
    ) == ["BTCUSDT__trade__2026-09-12"]


def test_every_committed_real_acknowledgement_is_valid():
    """The committed audit trail must satisfy the same validation the loader
    applies -- otherwise a real day would silently re-pause (or worse, a
    malformed ack would have been unpausing it)."""
    from data.lake_paths import LAKE_REGISTRY_ROOT
    from data.store import validate_dq_acknowledgement

    files = sorted((LAKE_REGISTRY_ROOT / "dq_acknowledgements").glob("*.json"))
    assert files
    for f in files:
        symbol, stream, date = f.stem.split("__")
        assert (
            validate_dq_acknowledgement(f, symbol=symbol, stream=stream, date=date)
            is None
        ), f


# --- WR-15 (03-REVIEW-ITER2.md): the verdict belongs to a manifest ---------


def _issue_second_manifest(lake_root: Path, registry_root: Path, date: str) -> dict:
    """A rebuild of `date` (e.g. WR-03's supersede): new part file, new
    manifest, by-date pointer moves to it; the old manifest keeps resolving."""
    df = pl.DataFrame(
        {"trade_id": [1, 2], "etime": [1_000, 2_000], "price": [1.0, 2.0]}
    )
    part = _write_partition(lake_root, f"curated/part-{date}-rebuild.parquet", date, df)
    return issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[part],
        code_hash="rebuild",
        registry_root=registry_root,
    )


def _write_bound_report(
    lake_root: Path, date: str, rows: list[tuple[str, str, str]]
) -> None:
    """rows: (manifest_id, check, dq_status) -- the post-WR-15 report shape."""
    path = dq_report_path(lake_root, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "date": [date] * len(rows),
            "symbol": ["BTCUSDT"] * len(rows),
            "stream": ["trade"] * len(rows),
            "manifest_id": [r[0] for r in rows],
            "check": [r[1] for r in rows],
            "dq_status": [r[2] for r in rows],
            "value": [0.0] * len(rows),
            "count": [None] * len(rows),
            "detail": [None] * len(rows),
        },
        schema={**REPORT_SCHEMA, "manifest_id": pl.Utf8},
    ).write_parquet(path)


def _load(manifest: dict, lake_root: Path, registry_root: Path) -> pl.DataFrame:
    return load_curated(
        manifest["manifest_id"],
        "BTCUSDT.trade",
        registry_root=registry_root,
        lake_root=lake_root,
    )


def test_superseded_manifest_does_not_load_under_its_successors_ok(tmp_path: Path):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    date = "2026-09-13"
    old = _issue_manifest(lake_root, registry_root, date)
    new = _issue_second_manifest(lake_root, registry_root, date)
    _write_bound_report(lake_root, date, [(new["manifest_id"], "gap_coverage", "ok")])

    assert _load(new, lake_root, registry_root).height == 2
    with pytest.raises(DQPauseError, match="missing"):
        _load(old, lake_root, registry_root)


def test_each_manifest_is_judged_only_by_its_own_rows(tmp_path: Path):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    date = "2026-09-13"
    old = _issue_manifest(lake_root, registry_root, date)
    new = _issue_second_manifest(lake_root, registry_root, date)
    _write_bound_report(
        lake_root,
        date,
        [
            (old["manifest_id"], "reconciliation", "failed"),
            (new["manifest_id"], "reconciliation", "ok"),
        ],
    )
    assert _load(new, lake_root, registry_root).height == 2
    with pytest.raises(DQPauseError, match="failed"):
        _load(old, lake_root, registry_root)


def test_legacy_report_without_manifest_id_cannot_vouch_for_a_superseded_manifest(
    tmp_path: Path,
):
    import os

    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    date = "2026-09-13"
    old = _issue_manifest(lake_root, registry_root, date)
    new = _issue_second_manifest(lake_root, registry_root, date)
    _write_report(lake_root, "BTCUSDT", "trade", date, "ok")  # no manifest_id column
    report = dq_report_path(lake_root, date)
    later = report.stat().st_mtime + 60  # deterministic "report newer than build"
    os.utime(report, (later, later))

    assert _load(new, lake_root, registry_root).height == 2  # pointer, report newer
    with pytest.raises(DQPauseError, match="legacy"):
        _load(old, lake_root, registry_root)


def test_legacy_report_older_than_the_pointer_manifest_does_not_vouch_for_it(
    tmp_path: Path,
):
    import os

    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    date = "2026-09-13"
    _write_report(lake_root, "BTCUSDT", "trade", date, "ok")  # scored an earlier build
    report = dq_report_path(lake_root, date)
    past = report.stat().st_mtime - 3600
    os.utime(report, (past, past))
    manifest = _issue_manifest(lake_root, registry_root, date)  # rebuilt afterwards
    with pytest.raises(DQPauseError, match="legacy"):
        _load(manifest, lake_root, registry_root)


def test_unknown_status_next_to_ok_is_failed_not_ok(tmp_path: Path):
    """03-REVIEW-ITER2.md IN-12."""
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    date = "2026-09-13"
    manifest = _issue_manifest(lake_root, registry_root, date)
    _write_bound_report(
        lake_root,
        date,
        [
            (manifest["manifest_id"], "gap_coverage", "ok"),
            (manifest["manifest_id"], "etime_plausibility", "FAILED"),
        ],
    )
    with pytest.raises(DQPauseError, match="unknown"):
        _load(manifest, lake_root, registry_root)
