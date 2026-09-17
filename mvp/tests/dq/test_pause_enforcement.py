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
import subprocess
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
from tools.git_env import scrubbed_git_env

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


GAP_FAILED = [{"check": "gap_coverage", "dq_status": "failed"}]
REPORT_MISSING = [{"check": "dq_report", "dq_status": "missing"}]


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=scrubbed_git_env(isolate_config=True),
    )


def _commit_registry(registry_root: Path, message: str = "ack") -> None:
    """The loader only honours a git-committed acknowledgement (WR-16), so
    the tmp registry is a git repository and every ack is committed."""
    registry_root.mkdir(parents=True, exist_ok=True)
    if not (registry_root / ".git").exists():
        _git(["init", "-q"], registry_root)
        _git(["config", "user.email", "t@t"], registry_root)
        _git(["config", "user.name", "t"], registry_root)
    _git(["add", "-A"], registry_root)
    _git(["commit", "-q", "--allow-empty", "-m", message], registry_root)


def _ack_body(
    symbol: str, stream: str, date: str, reason: str, acknowledged: list[dict]
) -> dict:
    return {
        "date": date,
        "symbol": symbol,
        "stream": stream,
        "reason": reason,
        "who": "test",
        "when": "2026-09-16T00:00:00Z",
        "acknowledged": acknowledged,
    }


def _write_acknowledgement(
    registry_root: Path,
    symbol: str,
    stream: str,
    date: str,
    reason: str,
    acknowledged: list[dict] = GAP_FAILED,
    *,
    commit: bool = True,
) -> Path:
    path = dq_acknowledgement_path(registry_root, symbol, stream, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_ack_body(symbol, stream, date, reason, acknowledged), indent=2)
    )
    if commit:
        _commit_registry(registry_root)
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
        registry_root,
        "BTCUSDT",
        "trade",
        date,
        "no DQ report generated for this date",
        REPORT_MISSING,
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
    _commit_registry(registry_root)  # committed: only the CONTENT is wrong

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
        # Content only: the git-committed requirement is enforced by the
        # loader, and this test runs inside pre-commit while an edited ack
        # is staged but not yet in HEAD.
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


def test_legacy_report_without_manifest_id_never_vouches_even_when_newer(
    tmp_path: Path,
):
    """03-REVIEW-ITER3.md WR-18: the legacy shim accepted a report with no
    `manifest_id` column for the by-date pointer's manifest whenever the file
    was newer than the build. Mtime is not evidence of what a report scored."""
    import os

    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    date = "2026-09-13"
    old = _issue_manifest(lake_root, registry_root, date)
    new = _issue_second_manifest(lake_root, registry_root, date)
    _write_report(lake_root, "BTCUSDT", "trade", date, "ok")  # no manifest_id column
    report = dq_report_path(lake_root, date)
    later = report.stat().st_mtime + 60  # "report newer than build"
    os.utime(report, (later, later))

    for manifest in (new, old):
        with pytest.raises(DQPauseError, match="no manifest_id column"):
            _load(manifest, lake_root, registry_root)


def test_touched_or_copied_stale_report_does_not_unpause_a_rebuilt_day(
    tmp_path: Path,
):
    """The reviewer's reproduction: a report that scored M1 sits beside M2
    after a supersede; `touch` (or `cp -R` without -p, rsync without -t, a
    restore, a volume move) makes it newer than M2's build."""
    import os
    import shutil

    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    date = "2026-09-13"
    m1 = _issue_manifest(lake_root, registry_root, date)
    _write_report(lake_root, "BTCUSDT", "trade", date, "ok")  # scored M1, legacy
    m2 = _issue_second_manifest(lake_root, registry_root, date)  # pointer -> M2
    assert m2["manifest_id"] != m1["manifest_id"]

    report = dq_report_path(lake_root, date)
    future = m2["built_at"] / 1e9 + 3600
    os.utime(report, (future, future))  # touch
    with pytest.raises(DQPauseError, match="missing"):
        _load(m2, lake_root, registry_root)

    copied = tmp_path / "lake_copy"
    shutil.copytree(lake_root, copied, copy_function=shutil.copyfile)  # no -p
    with pytest.raises(DQPauseError, match="missing"):
        _load(m2, copied, registry_root)


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


# --- WR-16 (03-REVIEW-ITER2.md): bound to its findings, and committed ------


def _failed_day(tmp_path: Path, rows: list[tuple[str, str]]):
    lake_root, registry_root = tmp_path / "lake", tmp_path / "registry"
    date = "2026-09-12"
    manifest = _issue_manifest(lake_root, registry_root, date)
    _write_bound_report(
        lake_root, date, [(manifest["manifest_id"], c, st) for c, st in rows]
    )
    return lake_root, registry_root, date, manifest


def test_ack_for_one_finding_does_not_unpause_a_different_finding(tmp_path: Path):
    """The original WR-01 scenario: an ack written for a degraded
    reconciliation must not cover the day after a rebuild fails it on
    build_stats."""
    lake_root, registry_root, date, manifest = _failed_day(
        tmp_path, [("build_stats", "failed")]
    )
    _write_acknowledgement(
        registry_root,
        "BTCUSDT",
        "trade",
        date,
        "reconciliation degraded 4.78 %",
        [{"check": "reconciliation", "dq_status": "degraded"}],
    )
    with pytest.raises(DQPauseError, match="build_stats=failed"):
        _load(manifest, lake_root, registry_root)


def test_ack_for_the_same_check_at_a_milder_status_does_not_cover_a_worse_one(
    tmp_path: Path,
):
    lake_root, registry_root, date, manifest = _failed_day(
        tmp_path, [("reconciliation", "failed")]
    )
    _write_acknowledgement(
        registry_root,
        "BTCUSDT",
        "trade",
        date,
        "known",
        [{"check": "reconciliation", "dq_status": "degraded"}],
    )
    with pytest.raises(DQPauseError, match="does not acknowledge"):
        _load(manifest, lake_root, registry_root)


def test_ack_must_cover_every_finding_of_the_day(tmp_path: Path):
    lake_root, registry_root, date, manifest = _failed_day(
        tmp_path, [("gap_coverage", "failed"), ("l1_sparsity", "degraded")]
    )
    _write_acknowledgement(registry_root, "BTCUSDT", "trade", date, "outage")
    with pytest.raises(DQPauseError, match="l1_sparsity=degraded"):
        _load(manifest, lake_root, registry_root)

    _write_acknowledgement(
        registry_root,
        "BTCUSDT",
        "trade",
        date,
        "outage",
        [*GAP_FAILED, {"check": "l1_sparsity", "dq_status": "degraded"}],
    )
    assert _load(manifest, lake_root, registry_root).height == 1


def test_untracked_acknowledgement_does_not_unpause(tmp_path: Path):
    lake_root, registry_root, date, manifest = _failed_day(
        tmp_path, [("gap_coverage", "failed")]
    )
    _commit_registry(registry_root, "registry without the ack")
    _write_acknowledgement(
        registry_root, "BTCUSDT", "trade", date, "outage", commit=False
    )
    with pytest.raises(DQPauseError, match="not committed"):
        _load(manifest, lake_root, registry_root)


def test_staged_but_uncommitted_acknowledgement_does_not_unpause(tmp_path: Path):
    lake_root, registry_root, date, manifest = _failed_day(
        tmp_path, [("gap_coverage", "failed")]
    )
    _commit_registry(registry_root, "registry without the ack")
    ack = _write_acknowledgement(
        registry_root, "BTCUSDT", "trade", date, "outage", commit=False
    )
    _git(["add", str(ack)], registry_root)
    with pytest.raises(DQPauseError, match="not committed"):
        _load(manifest, lake_root, registry_root)


def test_committed_ack_modified_in_the_working_tree_does_not_unpause(tmp_path: Path):
    lake_root, registry_root, date, manifest = _failed_day(
        tmp_path, [("build_stats", "failed")]
    )
    ack = _write_acknowledgement(
        registry_root,
        "BTCUSDT",
        "trade",
        date,
        "known",
        [{"check": "reconciliation", "dq_status": "degraded"}],
    )
    body = json.loads(ack.read_text())
    body["acknowledged"] = [{"check": "build_stats", "dq_status": "failed"}]
    ack.write_text(json.dumps(body, indent=2))  # edited, never committed
    with pytest.raises(DQPauseError, match="not committed"):
        _load(manifest, lake_root, registry_root)


def test_acknowledgement_outside_any_git_repository_does_not_unpause(tmp_path: Path):
    lake_root, registry_root, date, manifest = _failed_day(
        tmp_path, [("gap_coverage", "failed")]
    )
    _write_acknowledgement(
        registry_root, "BTCUSDT", "trade", date, "outage", commit=False
    )
    with pytest.raises(DQPauseError, match="not committed"):
        _load(manifest, lake_root, registry_root)


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("no acknowledged field", lambda b: b.pop("acknowledged")),
        ("acknowledged is empty", lambda b: b.update(acknowledged=[])),
        ("acknowledged is a string", lambda b: b.update(acknowledged="failed")),
        (
            "acknowledges ok",
            lambda b: b.update(acknowledged=[{"check": "x", "dq_status": "ok"}]),
        ),
        ("when in the future", lambda b: b.update(when="2999-01-01T00:00:00Z")),
    ],
)
def test_acknowledgement_content_is_bound_and_plausible(
    tmp_path: Path, label: str, mutate
):
    lake_root, registry_root, date, manifest = _failed_day(
        tmp_path, [("gap_coverage", "failed")]
    )
    body = _ack_body("BTCUSDT", "trade", date, "outage", GAP_FAILED)
    mutate(body)
    ack = dq_acknowledgement_path(registry_root, "BTCUSDT", "trade", date)
    ack.parent.mkdir(parents=True, exist_ok=True)
    ack.write_text(json.dumps(body))
    _commit_registry(registry_root)
    with pytest.raises(DQPauseError, match="acknowledge"):
        _load(manifest, lake_root, registry_root)


# --- WR-19 (03-REVIEW-ITER3.md): only the committed bytes count ------------


def _committed_ack_then_edited_under_index_flag(tmp_path: Path, flag: str):
    lake_root, registry_root, date, manifest = _failed_day(
        tmp_path, [("build_stats", "failed")]
    )
    ack = _write_acknowledgement(
        registry_root,
        "BTCUSDT",
        "trade",
        date,
        "known",
        [{"check": "reconciliation", "dq_status": "degraded"}],
    )
    _git(["update-index", flag, str(ack)], registry_root)
    body = json.loads(ack.read_text())
    body["acknowledged"] = [{"check": "build_stats", "dq_status": "failed"}]
    ack.write_text(json.dumps(body, indent=2))  # edited, never committed
    # The bypass: git itself reports the edited ack as unchanged.
    assert _git(["diff", "HEAD", "--", str(ack)], registry_root).stdout == ""
    return lake_root, registry_root, manifest


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_index_flag_hiding_an_edited_ack_does_not_unpause(tmp_path: Path, flag: str):
    lake_root, registry_root, manifest = _committed_ack_then_edited_under_index_flag(
        tmp_path, flag
    )
    with pytest.raises(DQPauseError, match="not committed"):
        _load(manifest, lake_root, registry_root)


def test_committed_symlink_ack_to_an_uncommitted_file_does_not_unpause(
    tmp_path: Path,
):
    lake_root, registry_root, date, manifest = _failed_day(
        tmp_path, [("gap_coverage", "failed")]
    )
    outside = tmp_path / "outside_ack.json"
    outside.write_text(
        json.dumps(_ack_body("BTCUSDT", "trade", date, "outage", GAP_FAILED), indent=2)
    )
    ack = dq_acknowledgement_path(registry_root, "BTCUSDT", "trade", date)
    ack.parent.mkdir(parents=True, exist_ok=True)
    ack.symlink_to(outside)
    _commit_registry(registry_root, "a committed symlink ack")
    assert "120000" in _git(["ls-files", "-s", "--", str(ack)], registry_root).stdout
    assert _git(["diff", "HEAD", "--", str(ack)], registry_root).stdout == ""

    with pytest.raises(DQPauseError, match="it is a symlink, not a regular file"):
        _load(manifest, lake_root, registry_root)


def test_symlinked_acknowledgement_directory_does_not_unpause(tmp_path: Path):
    lake_root, registry_root, date, manifest = _failed_day(
        tmp_path, [("gap_coverage", "failed")]
    )
    outside = tmp_path / "outside_acks"
    outside.mkdir()
    name = dq_acknowledgement_path(registry_root, "BTCUSDT", "trade", date).name
    (outside / name).write_text(
        json.dumps(_ack_body("BTCUSDT", "trade", date, "outage", GAP_FAILED), indent=2)
    )
    (registry_root / "dq_acknowledgements").symlink_to(outside)
    _commit_registry(registry_root, "a committed symlinked ack directory")
    with pytest.raises(DQPauseError, match="not committed"):
        _load(manifest, lake_root, registry_root)


def test_committed_regular_ack_still_unpauses_with_the_byte_check(tmp_path: Path):
    lake_root, registry_root, date, manifest = _failed_day(
        tmp_path, [("gap_coverage", "failed")]
    )
    _write_acknowledgement(registry_root, "BTCUSDT", "trade", date, "outage")
    assert _load(manifest, lake_root, registry_root).height == 1
