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

`quarantine_feature_partition` (05-05-PLAN.md, D-05-18/Q6) is the SECOND,
and only other, operation in the whole codebase permitted to join a path
under `lockbox/`: moving a features-tier partition's bytes into the
lockbox tier and issuing its lockbox-tier manifest. It never lifts or
re-applies the `chmod 0000` barrier either -- same posture as
`open_lockbox` above, same reason (`data/lockbox_POLICY.md`). Unlike
`open_lockbox`, it is never called by any Phase 5 code path or test beyond
its own; `mvp/harness/holdout_declare.py` orchestrates around it but the
function itself stays unexercised end-to-end until Phase 8 (D-05-17).
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import sqlite3
import time
from pathlib import Path

import polars as pl
from mlflow.entities import ViewType
from mlflow.tracking import MlflowClient

from data import lake_paths
from data.capture.config import DEFAULT_MIN_FREE_GB
from data.lake_paths import LAKE_REGISTRY_ROOT
from data.store import (
    FEATURES_TIER,
    by_date_index_path,
    issue_manifest,
    read_verified_partitions,
    resolve_manifest,
)
from tracking.mlflow_utils import (
    build_tracking_uri,
    compute_code_hash,
    compute_env_hash,
    start_tracked_run,
)

PKG_ROOT = Path(__file__).resolve().parents[1]

logger = logging.getLogger(__name__)

#: The tier name this module -- and only this module -- resolves manifests
#: for (`data.store.resolve_manifest(expected_tier=...)`, 03-REVIEW.md CR-04).
LOCKBOX_TIER = "lockbox"

__all__ = [
    "LockboxTokenError",
    "QuarantineError",
    "issue_token",
    "open_lockbox",
    "quarantine_feature_partition",
    "token_path",
]


class LockboxTokenError(ValueError):
    """Raised for any lockbox token protocol violation: missing token,
    requester mismatch, or an already-consumed token (checked against
    MLflow first, then the JSON's own `consumed_at` field)."""


class QuarantineError(ValueError):
    """Raised by `quarantine_feature_partition`: no features partition on
    record for the requested date (nothing to quarantine), or a manifest
    shape this function was not written for (more than one partition per
    day). Never raised for a `chmod 0000` `PermissionError` against the
    lockbox tier itself -- that is a plain `PermissionError`, not this."""


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


#: Tables every initialised MLflow SQLite tracking store has; a file without
#: them is not a store whose silence means anything.
MLFLOW_STORE_REQUIRED_TABLES: frozenset[str] = frozenset(
    {"alembic_version", "experiments", "runs", "tags"}
)

_SQLITE_HEADER = b"SQLite format 3\x00"

#: Duplicated from `harness.budget`/`harness.negative_log` (05-PATTERNS.md:
#: a private helper with a real behavioural contract, copied rather than
#: cross-imported). Unlike a segment name or a config fingerprint,
#: `token_id` IS human-chosen at `issue_token` call time -- this is the
#: module with the LARGEST exposure to 05-REVIEW.md WR-04's own finding
#: (unescaped f-string interpolation into an MLflow `filter_string`), even
#: though the review named only `harness.budget`/`harness.negative_log`;
#: fixed here too, per the project's own standing rule that every error
#: found gets fixed, not only the ones named.
_FILTER_SAFE_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _require_filter_safe(value: str, label: str) -> None:
    """Refuse (`LockboxTokenError`) a value about to be spliced into an
    MLflow `filter_string` via raw f-string interpolation -- see
    `harness.budget._require_filter_safe`'s identically named function for
    the full rationale. Checked BEFORE any MLflow client is constructed."""
    if not _FILTER_SAFE_RE.match(value):
        raise LockboxTokenError(
            f"{label} {value!r} contains a character outside "
            f"{_FILTER_SAFE_RE.pattern} -- refusing to splice it into an "
            "MLflow filter_string unescaped (05-REVIEW.md WR-04)"
        )


def _require_initialised_mlflow_store(store_file: Path) -> None:
    """Refuse (`LockboxTokenError`) a `mlflow.db` that is not an already
    initialised MLflow SQLite store (03-REVIEW-ITER2.md WR-12): a zero-byte
    file or a SQLite file without MLflow's schema used to be initialised by
    MLflow on first use as a fresh, empty store -- which then answered
    "never consumed". Opened read-only; never modifies the file."""
    problem: str | None = None
    try:
        with open(store_file, "rb") as fh:
            header = fh.read(len(_SQLITE_HEADER))
        if header != _SQLITE_HEADER:
            problem = f"no SQLite header ({store_file.stat().st_size} bytes)"
        else:
            uri = f"{store_file.resolve().as_uri()}?mode=ro"
            con = sqlite3.connect(uri, uri=True)
            try:
                tables = {
                    row[0]
                    for row in con.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                missing = MLFLOW_STORE_REQUIRED_TABLES - tables
                if missing:
                    problem = f"missing MLflow tables {sorted(missing)}"
                elif (
                    con.execute("SELECT COUNT(*) FROM alembic_version").fetchone()[0]
                    < 1
                ):
                    problem = "alembic_version is empty (schema never migrated)"
            finally:
                con.close()
    except (OSError, sqlite3.Error) as exc:
        problem = f"{exc.__class__.__name__}: {exc}"
    if problem is not None:
        raise LockboxTokenError(
            f"{store_file} is not an initialised MLflow store ({problem}) -- "
            "refusing to treat its silence as 'token never consumed'"
        )


def _require_canonical_tracking_root(tracking_root: str, allowed_root: Path) -> None:
    """Refuse a `tracking_root` that is not the project's canonical MLflow
    store (03-REVIEW-ITER2.md WR-12 remainder).

    `_require_initialised_mlflow_store` closed the empty/foreign-file
    fail-open, but any OTHER genuinely initialised MLflow database still
    answered "never consumed" about a token it has never heard of -- and the
    access run was then logged into that throwaway store, so the durable
    record never reached the real one. Pinning the root closes that: the
    one-look barrier now verifies it is reading the store it claims to.

    Compared after `Path.resolve()` on both sides, so `/tmp` vs
    `/private/tmp`, a trailing slash, and a symlink to the real store are
    the same root. `allowed_root` is resolved by
    `lake_paths.mlflow_tracking_root()` before it gets here.
    """
    resolved = Path(tracking_root).resolve()
    allowed = Path(allowed_root).resolve()
    if resolved != allowed:
        raise LockboxTokenError(
            f"tracking root {resolved} is not the project's canonical MLflow "
            f"store {allowed} -- refusing to ask a different store whether this "
            "token was consumed, and refusing to log the access run there"
        )


def _mlflow_has_consumed(
    token_id: str, tracking_root: str, *, allowed_root: str | None = None
) -> bool:
    """Return whether any MLflow run, in any experiment at `tracking_root`,
    carries `tags.lockbox_token_id == token_id` -- INCLUDING soft-deleted
    runs and runs in soft-deleted experiments (`ViewType.ALL`, 03-REVIEW.md
    WR-06: the MLflow default is `ACTIVE_ONLY`, so deleting the access run
    from the UI, a routine cleanup, used to re-arm the token).

    Searches ACROSS ALL experiments (not just `lockbox_access`).
    `MlflowClient().search_runs(...)` is used, never the pandas-returning
    `mlflow.search_runs()` convenience API (spec.md DONT).

    Refuses (`LockboxTokenError`) a `tracking_root` that does not ALREADY
    contain `mlflow.db` (WR-06), or whose `mlflow.db` is not an initialised
    MLflow SQLite store -- zero bytes, not SQLite, or missing MLflow's
    tables (03-REVIEW-ITER2.md WR-12): constructing a client there would
    silently create a fresh, empty store and answer "never consumed" -- a
    fail-open. The check runs before any MLflow object is constructed.

    Also refuses a `tracking_root` that is not the project's CANONICAL
    MLflow store (03-REVIEW-ITER2.md WR-12 remainder). "Initialised MLflow
    store" is not enough: any other project's perfectly valid store answers
    "never consumed" about a token it has never heard of, and the access run
    is then logged there, so the durable record never reaches the real one.

    The canonical root comes from `lake_paths.mlflow_tracking_root()`:
    `$AIHF_MLFLOW_TRACKING_ROOT` if set, else
    `DEFAULT_MLFLOW_TRACKING_ROOT`. `allowed_root` is a MODULE-PRIVATE
    parameter on a module-private function and is not reachable from the
    public API -- `open_lockbox` no longer has a
    `canonical_tracking_root=` keyword at all (03-REVIEW-FOLLOWUPS.md
    WR-04: one keyword on the public signature restored exactly the pre-fix
    behaviour the pin was written to remove, and nothing but a docstring
    said not to use it). A host whose store lives elsewhere sets the
    documented environment variable, which is a configuration decision
    rather than a per-call escape hatch.

    Any exception raised by `MlflowClient(...)`, `.search_experiments()`, or
    `.search_runs()` PROPAGATES UNMODIFIED -- `False` here means "the query
    succeeded and found nothing", never "the query could not be run".

    Residual, honestly-stated gap: if the real `mlflow.db` is deleted AND
    recreated empty (or replaced by another store) AND the token JSON's
    `consumed_at` stamp is reverted at the same time, this function cannot
    tell "never consumed" from "history destroyed". Either signal alone
    still catches it. Documented in `data/lockbox_POLICY.md`.
    """
    _require_filter_safe(token_id, "token_id")
    _require_canonical_tracking_root(
        tracking_root, lake_paths.mlflow_tracking_root(allowed_root)
    )
    store_file = Path(tracking_root).resolve() / "mlflow.db"
    if not store_file.exists():
        raise LockboxTokenError(
            f"tracking root {tracking_root} has no existing mlflow.db -- refusing "
            "to create a fresh store and treat the token as never consumed"
        )
    _require_initialised_mlflow_store(store_file)
    client = MlflowClient(build_tracking_uri(str(tracking_root)))
    experiment_ids = [
        exp.experiment_id for exp in client.search_experiments(view_type=ViewType.ALL)
    ]
    if not experiment_ids:
        return False
    runs = client.search_runs(
        experiment_ids,
        filter_string=f"tags.lockbox_token_id = '{token_id}'",
        run_view_type=ViewType.ALL,
    )
    return len(runs) > 0


#: Locks live in their own directory, NOT beside the token JSON
#: (03-REVIEW-ITER2.md IN-14): `lockbox_tokens/` is git-tracked, so a lock
#: left by a crash used to be picked up by `git add -A` and committed, after
#: which every clone refused that token until someone deleted it by hand.
#: This directory is gitignored.
LOCK_DIR_NAME = ".locks"


def lock_path_for(token_path_: Path) -> Path:
    """Where `_acquire_open_lock` puts the lock for a token JSON."""
    return token_path_.parent / LOCK_DIR_NAME / f"{token_path_.stem}.lock"


def _holder_liveness(lock_path: Path) -> str:
    """A human-readable hint about whether the recorded holder is still
    alive. A lock is never removed automatically on its strength -- it only
    tells the human which of the two situations they are in."""
    try:
        body = dict(
            field.split("=", 1)
            for field in lock_path.read_text().split()
            if "=" in field
        )
    except OSError:
        return "could not read the lock file"
    pid, host = body.get("pid"), body.get("host")
    if host != socket.gethostname():
        return f"held by pid {pid} on host {host!r}, not this host"
    try:
        os.kill(int(pid), 0)
    except (ValueError, TypeError):
        return f"lock file records no usable pid ({pid!r})"
    except ProcessLookupError:
        return f"pid {pid} on this host is NOT running -- the lock is stale"
    except PermissionError:
        return f"pid {pid} on this host is running (owned by another user)"
    return f"pid {pid} on this host IS still running -- do not remove the lock"


def _acquire_open_lock(path: Path, token_id: str) -> Path:
    """Exclusive-create the token's lock for the whole
    check-then-stamp-then-read sequence (03-REVIEW.md WR-06: two concurrent
    callers could both pass the consumed checks). A leftover lock after a
    crash fails CLOSED: the human confirms the token's MLflow record and
    removes it.

    The lock lives in the gitignored `lockbox_tokens/.locks/`, never beside
    the git-tracked token JSON (IN-14), and records `pid`, `host` and the
    open time so the refusal can say whether the holder is still alive
    instead of leaving the human to guess.
    """
    lock_path = lock_path_for(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        raise LockboxTokenError(
            f"an open of token {token_id} is already in progress (lock file "
            f"{lock_path} exists; {_holder_liveness(lock_path)}); if no other "
            "process holds it, confirm the token's MLflow record before "
            "removing the stale lock"
        ) from None
    with os.fdopen(fd, "w") as fh:
        fh.write(f"pid={os.getpid()} host={socket.gethostname()} at={time.time_ns()}\n")
    return lock_path


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

    1. Raise `LockboxTokenError` if the token file is absent. Then take an
       exclusive `<token>.lock` for everything below (WR-06), released on
       exit, and read the token JSON only under it (WR-12).
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

    lock_path = _acquire_open_lock(path, token_id)
    try:
        # Read the token only while holding the lock (03-REVIEW-ITER2.md
        # WR-12): a copy read before the lock may predate another caller's
        # consumed_at stamp.
        token = json.loads(path.read_text())
        if token["requested_by"] != requested_by:
            raise LockboxTokenError(
                f"token {token_id} was issued to requested_by="
                f"{token['requested_by']!r}, not {requested_by!r} -- refusing "
                "to open"
            )
        return _open_locked(
            token_id,
            token,
            path,
            purpose,
            tracking_root,
            lake_root=lake_root,
            registry_root=registry_root,
            min_free_gb=min_free_gb,
        )
    finally:
        lock_path.unlink(missing_ok=True)


def _open_locked(
    token_id: str,
    token: dict,
    path: Path,
    purpose: str,
    tracking_root: str,
    *,
    lake_root: Path | None,
    registry_root: Path | None,
    min_free_gb: float,
) -> pl.DataFrame:
    """Steps 3-8 of `open_lockbox`, run while holding the token's open lock."""

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

    # `min_free_gb` governs `resolved_lake_root` above ONLY (the physical
    # SSD lake -- multi-GB parquet partitions, legitimately sized like
    # capture's own DEFAULT_MIN_FREE_GB). The tracking root is a different
    # kind of root (kilobytes, an MLflow run) and gets its OWN floor from
    # `start_tracked_run`'s default (`tracking.mlflow_utils.
    # MLFLOW_MIN_FREE_GB`) -- passing this function's `min_free_gb` through
    # here would be the same category error 05-VERIFICATION-FIX.md's Gap 1
    # fixed in `harness.budget`/`harness.negative_log`; not reproduced here.
    with start_tracked_run(str(tracking_root), tags, "lockbox_access") as run:
        # (6) run_id written back immediately -- before the read.
        token["mlflow_run_id"] = run.info.run_id
        _atomic_write_json(path, token)

        # (7) resolve + read -- the one sanctioned lockbox/-path join site.
        manifest = resolve_manifest(
            segment_manifest_id,
            token["dataset"],
            registry_root=resolved_registry_root,
            lake_root=resolved_lake_root,
            expected_tier=LOCKBOX_TIER,
        )
        # IN-10: hash and parse the SAME buffer, never two opens.
        frames = read_verified_partitions(manifest, lake_root=resolved_lake_root)
        df = pl.concat(frames, how="vertical")

    return df


def _lockbox_feature_partition_path(lake_root: Path, symbol: str, date: str) -> Path:
    """`lake_root/lockbox/symbol=<symbol>/date=<date>/part-<ns>.parquet` --
    mirrors `features.tier.feature_partition_path`'s NO-`stream=`-level
    shape (a decision row is the merge of both curated streams; a
    per-stream lockbox path would invite a per-stream read that cannot
    exist), with `lockbox` replacing `features` as the tier segment. THE
    ONLY OTHER SITE IN THIS FILE PERMITTED TO JOIN A PATH UNDER `lockbox/`,
    besides `open_lockbox`'s own read in `_open_locked` above."""
    return (
        Path(lake_root)
        / LOCKBOX_TIER
        / f"symbol={symbol}"
        / f"date={date}"
        / f"part-{time.time_ns()}.parquet"
    )


def quarantine_feature_partition(
    date: str,
    *,
    symbol: str,
    lake_root: Path,
    registry_root: Path,
    code_hash: str,
    reason: str,
) -> dict:
    """Move `date`'s CURRENT features-tier partition into the lockbox tier
    and issue its lockbox-tier manifest -- the ONE new operation D-05-18/Q6
    calls for, and (with `open_lockbox`'s own read above) one of only two
    sites in the entire codebase permitted to join a path under `lockbox/`
    (enforced by `tools/check_lockbox_containment.py` against every other
    `*.py` file in `mvp/`, this one and the scanner itself excepted).

    `mvp/harness/holdout_declare.py` orchestrates the D_lock/D_lock-1 pair
    and the holdout-registry write AROUND this call; this function itself
    knows nothing about "declaration" -- it moves exactly the one date it
    is given, once, and returns the new manifest.

    Order of operations (mirrors `open_lockbox`'s own check-then-act
    discipline above: resolve and verify BEFORE anything is created or
    moved):

    1. Resolve `date`'s CURRENT features-tier manifest via the by-date
       index (a plain existence check on the pointer file) and
       `resolve_manifest(expected_tier=FEATURES_TIER)` -- which re-hashes
       the manifest body AND every partition's on-disk bytes before
       returning it. Refuses (`QuarantineError`) if no by-date pointer
       exists for `(symbol, date)` -- nothing built, nothing to quarantine
       -- or if the resolved manifest names anything other than exactly
       one partition (a decision-row day is one file; this function is not
       written for any other shape).
    2. `os.replace` the partition file straight from its features-tier path
       to a NEW lockbox-tier path (`_lockbox_feature_partition_path`
       above) -- an atomic rename on the same filesystem (both tiers are
       siblings under the same `lake_root`), never a read-then-write-then-
       delete: the bytes are relocated, not copied and not re-read. The
       new manifest's `sha256`/`rows`/`etime_min`/`etime_max` are carried
       over VERBATIM from the just-verified original entry (step 1 already
       proved those bytes match that digest; re-hashing a partition that
       can run into the hundreds of MiB a second time, on every real
       invocation, buys nothing `resolve_manifest` did not already buy).
       Only `size_bytes`/`mtime_ns` are re-`stat()`'d at the new path,
       since a rename can change them even though the content is
       untouched.
    3. Issue the new manifest: `dataset=f"{symbol}.features"` -- the SAME
       dataset name the ordinary features tier uses; only `tier="lockbox"`
       differs, so this manifest lands in the SAME
       `manifests/{symbol}.features/` directory as ordinary features-tier
       manifests, distinguished by its own content-hash id and by the
       `tier` field every caller must pass as `expected_tier` to
       `resolve_manifest`. `inputs` RE-CITES the original manifest's own
       `inputs` list verbatim (the curated manifests the quarantined day
       was built from, chosen over an empty list) so the lockbox
       manifest keeps its provenance chain back to curated bytes that are
       themselves never moved -- the plan's stated alternative
       (`inputs=[]`) would have severed that chain for no reason the move
       itself requires. Step 3 is wrapped so that any exception it raises
       triggers an `os.replace` BACK to `old_path` before re-raising
       (05-REVIEW.md WR-01) -- the pre-call state (old manifest resolves,
       nothing at the new path) is restored rather than left with the
       bytes moved and no manifest naming them anywhere. This recovers
       from an `issue_manifest` exception only, not from a hard kill
       between the move and this recovery running -- see that function
       call's own inline comment for the residual, stated honestly.

    NEVER CALLS `chmod`/`os.chmod` ANYWHERE IN ITS BODY (source-inspected
    by a dedicated red-proof test): the human lifts/re-applies the
    `chmod 0000` barrier out-of-band, per `data/lockbox_POLICY.md`. This
    function's job ends at the manifest.

    NAMED RESIDUAL FOR PHASE 8 -- STATED, NOT SOLVED. Once this returns,
    the OLD features-tier manifest for `date` no longer resolves
    (`resolve_manifest(..., expected_tier=FEATURES_TIER)` now raises
    `ManifestHashMismatch` with `"<missing>"`, since its partition file is
    gone): but that manifest is never rewritten or deleted, only orphaned
    -- a committed, append-only artifact whose named bytes are no longer on
    disk. `tools/check_no_manifest_rewrite --full` will flag every date
    this function has quarantined as a violation from the moment it runs,
    unless Phase 8 also designs a mechanism to RETIRE (never rewrite) the
    old manifest. That design is explicitly not this function's job.
    """
    dataset = f"{symbol}.{FEATURES_TIER}"
    pointer_path = by_date_index_path(
        registry_root, dataset, symbol, FEATURES_TIER, date
    )
    if not pointer_path.exists():
        raise QuarantineError(
            f"quarantine_feature_partition: no features partition on record "
            f"for {symbol} {date} (no by-date pointer at {pointer_path}) -- "
            "nothing to quarantine"
        )
    manifest_id = json.loads(pointer_path.read_text())["manifest_id"]
    manifest = resolve_manifest(
        manifest_id,
        dataset,
        registry_root=registry_root,
        lake_root=lake_root,
        expected_tier=FEATURES_TIER,
    )
    partitions = manifest["partitions"]
    if len(partitions) != 1:
        raise QuarantineError(
            f"quarantine_feature_partition: manifest {manifest_id[:12]} for "
            f"{symbol} {date} names {len(partitions)} partitions, expected "
            "exactly 1 -- a decision-row day is one file"
        )
    old_entry = partitions[0]
    old_path = Path(lake_root) / old_entry["path"]

    new_path = _lockbox_feature_partition_path(lake_root, symbol, date)
    new_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info(
        "quarantine_feature_partition: moving symbol=%s date=%s manifest=%s reason=%r",
        symbol,
        date,
        manifest_id[:12],
        reason,
    )
    os.replace(old_path, new_path)
    st = new_path.stat()

    new_entry = {
        "date": date,
        "path": str(new_path.relative_to(Path(lake_root))),
        "sha256": old_entry["sha256"],
        "rows": old_entry["rows"],
        "size_bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "etime_min": old_entry["etime_min"],
        "etime_max": old_entry["etime_max"],
    }
    # 05-REVIEW.md WR-01: the bytes have already moved (the `os.replace`
    # above is irreversible on its own -- the old path is gone). If
    # `issue_manifest` raises ANYTHING below -- disk-full on the JSON
    # write, a partition-path collision, any of `data.store.issue_manifest`'s
    # own several `ValueError`s -- the bytes would otherwise sit at the new
    # lockbox path with NO manifest anywhere naming them, while the OLD
    # features-tier manifest is now permanently broken (its partition file
    # no longer exists at the recorded path). Move the bytes back to
    # `old_path` on any such failure, so the pre-call state (old manifest
    # resolves, nothing at the new path) is restored rather than left in
    # an unregistered, doubly-broken state -- then re-raise so the caller
    # still sees the original failure.
    #
    # RESIDUAL, STATED HONESTLY: this only recovers from an exception
    # `issue_manifest` itself raises. A hard kill (SIGKILL, power loss)
    # between the `os.replace` above and this `try` block's own completion
    # leaves the bytes at `new_path` with no manifest -- recoverable only
    # by a human re-running `issue_manifest` by hand against the orphaned
    # file, exactly as 05-REVIEW.md's finding describes. Nothing short of a
    # transactional filesystem closes that window; it is not attempted here.
    try:
        return issue_manifest(
            dataset=dataset,
            symbol=symbol,
            stream=FEATURES_TIER,
            tier=LOCKBOX_TIER,
            schema_version=manifest["schema_version"],
            inputs=list(manifest.get("inputs") or []),
            partitions=[new_entry],
            code_hash=code_hash,
            registry_root=registry_root,
        )
    except BaseException:
        logger.error(
            "quarantine_feature_partition: issue_manifest failed after moving "
            "symbol=%s date=%s to %s -- restoring the partition to %s",
            symbol,
            date,
            new_path,
            old_path,
        )
        os.replace(new_path, old_path)
        assert old_path.exists(), (
            "quarantine_feature_partition: restore itself failed -- "
            f"{old_path} does not exist after the rollback os.replace"
        )
        raise
