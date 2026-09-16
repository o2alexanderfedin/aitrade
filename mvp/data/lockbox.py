"""The sole audited path to `lake/lockbox/` in the whole codebase.

Two independent barriers stand between the default loader (`data/store.py`)
and quarantined data (03-RESEARCH.md SS F, Pitfall 14 in PITFALLS.md):

1. **Missing code path**: `data/store.py` never constructs a path under
   `lockbox/`. This module is the only place permitted to join a path under
   `lockbox/`, enforced mechanically by `tools/check_lockbox_containment.py`
   (an AST scan, not a grep) against every other `*.py` file in `mvp/`.
2. **Physical `chmod 0000`** on the lockbox directory -- a genuine, MEASURED
   second barrier (03-RESEARCH.md Pitfall 5): it blocks a same-uid
   `open()`/`iterdir()`/`glob()` with `PermissionError`. It does NOT stop a
   determined same-uid actor, who can always `chmod` it back. This module
   never lifts that barrier itself -- lifting it for a human-invoked gate
   evaluation is an out-of-band operational step, documented in
   `data/lockbox_POLICY.md`, not something this code automates.

Scope, stated honestly (do not oversell this elsewhere): this delivers
accident-proofing plus an audit trail against a same-uid, NON-adversarial
actor (an agent enthusiastically globbing "all available data", per
PITFALLS #14) -- not protection against a determined same-uid agent.
Agent-proofing (a sandbox that simply doesn't mount the lockbox path) is
Phase 10's job.

Unlock-token protocol ("one look"): a token is a git-committed JSON file
under `LAKE_REGISTRY_ROOT / "lockbox_tokens" / <token_id>.json` (see
`data/lake_paths.py`'s docstring for why the registry root -- not the
physical SSD-only `lake/lockbox/tokens/` CONTEXT.md's literal path names --
is where audit JSON lives: it must be git-committed so "every look appears
in a diff" is literally true). `open_lockbox` checks consumption TWICE, in
a specific order:

  1. Query MLflow FIRST (`_mlflow_has_consumed`) -- the DURABLE record. A
     same-uid `git checkout -- <token>.json` silently reverts the JSON's own
     `consumed_at` field (03-RESEARCH.md Pitfall 4); MLflow, already this
     project's system of record, cannot be reverted by the same operation.
  2. Then check the JSON's own `consumed_at` field -- cheaper, and still
     useful when MLflow itself is unreachable.

Any exception `_mlflow_has_consumed`'s own query raises PROPAGATES UNMODIFIED
out of `open_lockbox` -- it is never caught and collapsed to "not consumed".
`False` means "asked MLflow and it said no"; an exception means "could not
ask MLflow", and those are not the same thing. Pointing `open_lockbox` at a
reset or unreachable tracking root must never silently re-arm a previously
consumed token.

`open_lockbox` stamps `consumed_at` in the JSON atomically BEFORE returning
any rows (before even attempting the read) -- a crash between the stamp and
the read leaves the token looking burned, never silently reusable. This is
the deliberately safer failure direction.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import polars as pl
from mlflow.tracking import MlflowClient

from data import lake_paths
from data.capture.config import DEFAULT_MIN_FREE_GB
from data.lake_paths import LAKE_REGISTRY_ROOT
from data.store import resolve_manifest
from tracking.mlflow_utils import (
    build_tracking_uri,
    compute_code_hash,
    compute_env_hash,
    start_tracked_run,
)

PKG_ROOT = Path(__file__).resolve().parents[1]

__all__ = [
    "LockboxTokenError",
    "issue_token",
    "open_lockbox",
    "token_path",
]


class LockboxTokenError(ValueError):
    """Raised for any lockbox token protocol violation: missing token,
    requester mismatch, or an already-consumed token (checked against
    MLflow first, then the JSON's own `consumed_at` field)."""


def token_path(token_id: str, *, registry_root: Path | None = None) -> Path:
    """Single source of truth for a lockbox token JSON's on-disk path.

    `<registry_root> / "lockbox_tokens" / <token_id>.json`, defaulting
    `registry_root` to the real, git-committed `LAKE_REGISTRY_ROOT` when not
    given -- a correction from CONTEXT.md's literal
    `lake/lockbox/tokens/<id>.json` (that root lives on the SSD, outside
    git; see `data/lake_paths.py`'s module docstring), matching the
    correction Plan 02 already made for manifests. `registry_root` is
    overridable (not hardcoded) so tests can point token storage at a
    `tmp_path` fixture instead of writing into the real, git-committed
    registry -- the same hermeticity `data.store`'s tests already rely on
    for manifests.
    """
    root = registry_root if registry_root is not None else LAKE_REGISTRY_ROOT
    return Path(root) / "lockbox_tokens" / f"{token_id}.json"


def _atomic_write_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(body, sort_keys=True, indent=2))
    tmp_path.replace(path)


def issue_token(
    token_id: str,
    segment_manifest_id: str,
    dataset: str,
    purpose: str,
    gate: str,
    requested_by: str,
    *,
    registry_root: Path | None = None,
) -> dict:
    """Write a new, unconsumed token JSON to
    `token_path(token_id, registry_root=registry_root)`.

    Refuses to overwrite an existing `token_id` (write-once, same posture as
    every other artifact in this data lake). `dataset` is NOT part of
    CONTEXT.md's literal token schema -- added here because `open_lockbox`
    reuses `data.store.resolve_manifest(manifest_id, dataset, ...)`, which
    requires a `dataset` to resolve a manifest's on-disk path
    (`registry_root/manifests/<dataset>/<manifest_id>.json`); the schema
    cannot omit it and still be resolvable (see the plan's SUMMARY,
    Deviations, for the full justification).

    Does not itself commit the file to git -- the caller's normal commit
    flow does that, which is what makes "every look appears in a diff" true
    (the ISSUANCE is a diff; `open_lockbox`'s later `consumed_at` stamp is a
    second diff on the same file).
    """
    path = token_path(token_id, registry_root=registry_root)
    if path.exists():
        raise LockboxTokenError(f"lockbox token already exists: {token_id}")
    body = {
        "token_id": token_id,
        "segment_manifest_id": segment_manifest_id,
        "dataset": dataset,
        "purpose": purpose,
        "gate": gate,
        "requested_by": requested_by,
        "created_at": time.time_ns(),
        "consumed_at": None,
        "mlflow_run_id": None,
    }
    _atomic_write_json(path, body)
    return body


def _mlflow_has_consumed(token_id: str, tracking_root: str) -> bool:
    """Return whether any MLflow run, in any experiment at `tracking_root`,
    carries `tags.lockbox_token_id == token_id`.

    Searches ACROSS ALL experiments (not just `lockbox_access`) -- strictly
    more conservative than scoping to one experiment name, and free at this
    project's MLflow scale. `MlflowClient().search_runs(...)` is used, never
    the pandas-returning `mlflow.search_runs()` convenience API (spec.md
    DONT).

    Any exception raised by `MlflowClient(...)`, `.search_experiments()`, or
    `.search_runs()` PROPAGATES UNMODIFIED -- this function never catches a
    query failure and returns `False`. `False` here means "the query
    succeeded and found nothing", not "the query could not be run"; an
    unreachable or reset tracking root must refuse the open, not silently
    treat it as never-consumed.

    Residual, honestly-stated gap (not engineered around here): if the
    tracking root is a FRESH, empty, but otherwise reachable sqlite path
    (e.g. the mlflow.db file was deleted and MLflow silently recreates an
    empty store), `search_experiments()` succeeds and returns `[]` -- this
    is indistinguishable from "genuinely never consumed" by this function.
    That failure mode requires the JSON stamp to ALSO have been reverted at
    the same time to actually re-arm a token; either signal alone (a
    reachable, non-empty tracking root OR an unreverted JSON stamp) still
    catches it. Documented in `data/lockbox_POLICY.md`.
    """
    client = MlflowClient(build_tracking_uri(str(tracking_root)))
    experiment_ids = [exp.experiment_id for exp in client.search_experiments()]
    if not experiment_ids:
        return False
    runs = client.search_runs(
        experiment_ids,
        filter_string=f"tags.lockbox_token_id = '{token_id}'",
    )
    return len(runs) > 0


def open_lockbox(
    token_id: str,
    purpose: str,
    requested_by: str,
    tracking_root: str,
    *,
    lake_root: Path | None = None,
    registry_root: Path | None = None,
    min_free_gb: float = DEFAULT_MIN_FREE_GB,
) -> pl.DataFrame:
    """One-look unlock: resolve the token's segment manifest and return its
    rows exactly once.

    Order of operations (exact, per 03-RESEARCH.md Pitfall 4 -- do not
    reorder):

    1. Read the token file; raise `LockboxTokenError` if absent.
    2. Verify `requested_by` matches the token's own stored `requested_by`
       -- an identity check beyond CONTEXT.md's literal spec (Rule 2:
       missing critical -- the token names who it was issued to; silently
       letting a different identity consume it would make "every look
       appears in a diff, attributable to who requested it" false).
    3. Call `_mlflow_has_consumed` FIRST (the durable check) -- raise if
       `True`. Any exception `_mlflow_has_consumed` itself raises (a query
       failure) PROPAGATES straight out of this function, uncaught.
    4. Check the JSON's own `consumed_at` field -- raise if already set
       (cheaper belt-and-suspenders check, still useful when MLflow is
       unreachable).
    5. Stamp `consumed_at` in the JSON atomically, BEFORE returning any
       rows or even attempting the read -- a crash after this point leaves
       the token looking burned, never silently reusable.
    6. Start an MLflow run via `start_tracked_run` (the project's ONLY
       MLflow entry point -- never a second `mlflow.start_run` call) tagged
       with all 8 mandatory keys plus `lockbox_access`, `lockbox_token_id`,
       `lockbox_purpose`. Write the run's `run_id` back into the token JSON
       immediately (before the read, for forensics if the read then fails).
    7. Resolve the segment's manifest via `data.store.resolve_manifest`
       (immutability/hash verification applies here too -- lockbox
       partitions are no less protected than curated ones) and read its
       partitions from `lockbox/`-rooted paths. THIS FUNCTION IS THE ONLY
       PLACE IN THE ENTIRE CODEBASE PERMITTED TO JOIN A PATH UNDER
       `lockbox/` (enforced by `tools/check_lockbox_containment.py`).
    8. Return the loaded DataFrame.
    """
    path = token_path(token_id, registry_root=registry_root)
    if not path.exists():
        raise LockboxTokenError(f"lockbox token not found: {token_id}")
    token = json.loads(path.read_text())

    if token["requested_by"] != requested_by:
        raise LockboxTokenError(
            f"token {token_id} was issued to requested_by="
            f"{token['requested_by']!r}, not {requested_by!r} -- refusing "
            "to open"
        )

    # (3) DURABLE check first -- query error propagates uncaught.
    if _mlflow_has_consumed(token_id, tracking_root):
        raise LockboxTokenError(
            f"token {token_id} already consumed (MLflow record found for "
            "tags.lockbox_token_id) -- refusing second look"
        )

    # (4) Cheaper JSON-side check, still useful if MLflow is unreachable.
    if token.get("consumed_at") is not None:
        raise LockboxTokenError(
            f"token {token_id} already consumed at "
            f"{token['consumed_at']} (JSON record) -- refusing second look"
        )

    # (5) Stamp BEFORE returning any rows -- safer failure direction.
    token["consumed_at"] = time.time_ns()
    _atomic_write_json(path, token)

    resolved_lake_root = (
        Path(lake_root)
        if lake_root is not None
        else lake_paths.lake_root(min_free_gb=min_free_gb)
    )
    resolved_registry_root = (
        Path(registry_root) if registry_root is not None else LAKE_REGISTRY_ROOT
    )

    segment_manifest_id = token["segment_manifest_id"]
    tags = {
        "code_hash": compute_code_hash(),
        "data_hash": segment_manifest_id,
        "seed": "n/a",
        "env_hash": compute_env_hash(PKG_ROOT / "uv.lock"),
        "segment_manifest_id": segment_manifest_id,
        "model_class": "n/a",
        "fold_config": "n/a",
        "stage": "lockbox_access",
        "lockbox_access": "true",
        "lockbox_token_id": token_id,
        "lockbox_purpose": purpose,
    }

    with start_tracked_run(
        str(tracking_root), tags, "lockbox_access", min_free_gb=min_free_gb
    ) as run:
        # (6) run_id written back immediately -- before the read.
        token["mlflow_run_id"] = run.info.run_id
        _atomic_write_json(path, token)

        # (7) resolve + read -- the one sanctioned lockbox/-path join site.
        manifest = resolve_manifest(
            segment_manifest_id,
            token["dataset"],
            registry_root=resolved_registry_root,
            lake_root=resolved_lake_root,
        )
        frames = [
            pl.read_parquet(resolved_lake_root / part["path"])
            for part in manifest["partitions"]
        ]
        df = pl.concat(frames, how="vertical")

    return df
