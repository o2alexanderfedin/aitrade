"""RP-3: the phase's subtlest red-proof.

A containment test that merely greps the loader's source for the string
"lockbox" proves nothing about REACHABILITY. This module's tests are
designed to actually attempt a read against a synthetic, `chmod 0000`'d
quarantined segment -- and the manual RP-3 drill (recorded in the plan
SUMMARY, not as a permanent test parameter) proves the assertion itself is
genuinely conditioned on the permission bit, not trivially true: with the
`os.chmod(..., 0o000)` line in `_quarantined_segment` temporarily commented
out, `test_default_loader_cannot_reach_lockbox` must FAIL; restored, it
must PASS.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import mlflow
import polars as pl
import pytest
from mlflow.tracking import MlflowClient

from data.lockbox import LockboxTokenError, issue_token, open_lockbox
from data.store import issue_manifest, load_curated
from tracking.mlflow_utils import build_tracking_uri


@pytest.fixture(autouse=True)
def _end_any_active_run():
    yield
    if mlflow.active_run() is not None:
        mlflow.end_run()


def _write_partition(lake_root: Path, rel_path: str, df: pl.DataFrame) -> dict:
    final_path = lake_root / rel_path
    final_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(final_path, compression="zstd")
    st = final_path.stat()
    return {
        "date": "2026-09-12",
        "path": rel_path,
        "sha256": hashlib.sha256(final_path.read_bytes()).hexdigest(),
        "rows": df.height,
        "size_bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "etime_min": int(df["etime"].min()),
        "etime_max": int(df["etime"].max()),
    }


@pytest.fixture
def _quarantined_segment(tmp_path: Path):
    """Build a synthetic, curated-shaped Parquet partition under
    `tmp_path/lockbox/...`, then `chmod 0000` that directory -- a real,
    measured physical barrier (03-RESEARCH.md Pitfall 5), not a
    string-absence proxy.

    Teardown ALWAYS chmods back to a removable permission before `tmp_path`
    is cleaned up -- pytest cannot remove a `0o000` directory itself.

    RP-3 DRILL LINE: the `os.chmod(lockbox_dir, 0o000)` call directly below
    is the one manually commented out, once, for the RP-3 drill transcribed
    in the plan SUMMARY. Do not remove it permanently -- it is the barrier
    this whole module exists to test.
    """
    lake_root = tmp_path / "lake"
    lockbox_dir = lake_root / "lockbox"
    df = pl.DataFrame(
        {"trade_id": [1, 2], "etime": [1_000, 2_000], "price": [1.0, 2.0]}
    )
    rel_path = "lockbox/symbol=BTCUSDT/stream=trade/date=2026-09-12/part-1.parquet"
    part = _write_partition(lake_root, rel_path, df)

    os.chmod(lockbox_dir, 0o000)  # <-- RP-3 drill line
    try:
        yield lake_root, lockbox_dir, part
    finally:
        os.chmod(lockbox_dir, 0o755)


# --- RP-3, direction 1: the default loader cannot reach a chmod'd segment ---


@pytest.mark.skipif(
    os.geteuid() == 0,
    reason="root ignores POSIX permission bits; this test would fail for the wrong reason",
)
def test_default_loader_cannot_reach_lockbox(_quarantined_segment):
    """Behavioral reachability assertion, not a source-code-absence check
    (that is `tools/check_lockbox_containment.py`'s job, exercised
    separately). `Path.iterdir()` against a `chmod 0000` directory raises
    `PermissionError` via `scandir` -- measured on this machine's APFS
    volume; `glob.glob()` was measured to instead silently return `[]`,
    which `pytest.raises` cannot catch, so it is deliberately not used
    here."""
    _lake_root, lockbox_dir, _part = _quarantined_segment
    with pytest.raises(PermissionError):
        list(lockbox_dir.iterdir())


# --- RP-3, direction 2 (independence check): chmod alone blocks a --------
# --- lockbox-pointed manifest read through the ORDINARY loader -----------


def test_load_curated_against_lockbox_manifest_is_blocked_by_chmod(
    tmp_path: Path, _quarantined_segment
):
    """Constructs a fixture manifest whose `partitions[].path` deliberately
    points INTO the `chmod 0000`'d `lockbox/` fixture directory --
    something `store.py`'s own code never does by construction (Plan 05's
    "no code path" guardrail already proves that absence) -- and calls
    `store.load_curated` DIRECTLY against that manifest, bypassing the
    missing-code-path control on purpose. Asserts `PermissionError`, not
    `ManifestHashMismatch` or a silent empty read -- proving the chmod
    barrier is a genuinely SECOND, INDEPENDENT layer, not decoration behind
    the loader's own refusal."""
    lake_root, _lockbox_dir, part = _quarantined_segment
    registry_root = tmp_path / "registry"

    manifest = issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="lockbox",
        schema_version=1,
        inputs=[],
        partitions=[part],
        code_hash="deadbeef",
        registry_root=registry_root,
    )

    with pytest.raises(PermissionError):
        load_curated(
            manifest["manifest_id"],
            "BTCUSDT.trade",
            registry_root=registry_root,
            lake_root=lake_root,
        )


# --- RP-3's formal token requirement (reuses Task 1's scenario) ----------


def test_token_one_look_red_proof(tmp_path: Path):
    """The phase's formal RP-3 token requirement: mark a token consumed,
    attempt `open_lockbox` again, assert it raises, and confirm the MLflow
    tag `lockbox_token_id` is present on EXACTLY ONE run for that token --
    not two (a second, successful look would double the run count)."""
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    tracking_root = tmp_path / "mlflow_root"
    tracking_root.mkdir()

    df = pl.DataFrame(
        {"trade_id": [1, 2], "etime": [1_000, 2_000], "price": [1.0, 2.0]}
    )
    part = _write_partition(
        lake_root,
        "lockbox/symbol=BTCUSDT/stream=trade/date=2026-09-12/part-rp3.parquet",
        df,
    )
    manifest = issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="lockbox",
        schema_version=1,
        inputs=[],
        partitions=[part],
        code_hash="deadbeef",
        registry_root=registry_root,
    )
    issue_token(
        token_id="rp3-token",
        segment_manifest_id=manifest["manifest_id"],
        dataset="BTCUSDT.trade",
        purpose="RP-3 red-proof",
        gate="RP-3",
        requested_by="alex",
        registry_root=registry_root,
    )

    df1 = open_lockbox(
        "rp3-token",
        "RP-3 red-proof",
        "alex",
        str(tracking_root),
        lake_root=lake_root,
        registry_root=registry_root,
        min_free_gb=0.0,
    )
    assert df1.height == 2

    with pytest.raises(LockboxTokenError):
        open_lockbox(
            "rp3-token",
            "RP-3 red-proof",
            "alex",
            str(tracking_root),
            lake_root=lake_root,
            registry_root=registry_root,
            min_free_gb=0.0,
        )

    client = MlflowClient(build_tracking_uri(str(tracking_root)))
    exp = client.get_experiment_by_name("lockbox_access")
    assert exp is not None
    runs = client.search_runs(
        [exp.experiment_id], filter_string="tags.lockbox_token_id = 'rp3-token'"
    )
    assert len(runs) == 1
