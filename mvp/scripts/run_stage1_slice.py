"""The Stage-1 regression slice's operator interface: five flags, and only
two of them can ever spend a look.

NEVER COLLECTED BY PYTEST -- run manually from `mvp/`:

    ./.venv/bin/python3 -m scripts.run_stage1_slice --select
    ./.venv/bin/python3 -m scripts.run_stage1_slice --freeze
    ./.venv/bin/python3 -m scripts.run_stage1_slice --spend-val-look

WHY `NUMBA_CACHE_DIR` IS PINNED AT THE TOP OF THIS FILE, BEFORE ANY IMPORT.
`tests/conftest.py` pins it for the pytest path only, and this is the
script's OWN invocation path. With the variable unset, `@njit(cache=True)`
writes its `.nbi`/`.nbc` files into the `__pycache__` NEXT TO THE DEFINING
SOURCE -- i.e. into `mvp/features/__pycache__/`, a gitignored directory where
only a repo walk can see them (STATE.md records this as a Phase 4 blocker).
`setdefault`, and the same expression `tests/conftest.py` uses, so a
developer or CI job that already pinned its own cache dir keeps it.

THE ONE INVERSION VERSUS `scripts/holdout_declare_dry_run_real_lake.py`, said
here so nobody "fixes" it. That script deliberately avoids
`data.lake_paths.lake_root()` because `lake_root()` `mkdir`s and
write-probes, and that script must touch NOTHING. This script WRITES -- a
`features_norm` artifact, a prediction table, MLflow runs -- so calling
`lake_root()` is correct here. But it is called INSIDE `main()`, never as an
argparse default: as a default expression it would fire on `--help` and on
every parse error, which is the one thing `--help` must not do.

THE FIVE FLAGS, AND WHICH OF THEM COSTS SOMETHING.

- `--select` spends the FIVE OOF looks and writes no registry body.
- `--freeze` costs NO look: it refits the winner from the already-cached
  train frame and writes the frozen body.
- `--spend-val-look` spends THE ONE HONEST LOOK, behind four independent
  refusals.
- `--resume-from-cache` spends NOTHING and is structurally incapable of
  spending anything: it calls `run_slice(mode="val")` against a val cache
  that already exists, so `models.cache.materialize_once` takes its cache-hit
  branch and never reaches the accessor.
- `--respend-val-look --reason "<diagnosis>"` CAN spend a second look, and is
  the only flag besides `--spend-val-look` that can.

THE FIVE STATES PARTITION, WHICH IS WHY THERE ARE FIVE FLAGS. Read
`(val cache present?, look_count(val))`:

| state | cache | count | the flag that owns it |
|---|---|---|---|
| nothing paid for | absent | 0 | `--spend-val-look` |
| paid for, frame present | present | >= 1 | `--resume-from-cache` |
| paid for, frame LOST | absent | >= 1 | `--respend-val-look` |

The third row is the state that made `--respend-val-look` necessary: a crash
between `harness.budget.record_look` committing its run and
`materialize_once` writing the Parquet leaves the look spent and no frame.
`--spend-val-look` refuses it (the count is not 0) and `--resume-from-cache`
refuses it (no cache), so without a flag for it the operator would be editing
this file at the single most consequential moment in the phase.

`--resume-from-cache` REQUIRES `look_count(val) >= 1`, NOT `== 1`. After a
successful `--respend-val-look` the count is 2, and a crash at step 8 or 9 of
THAT run leaves a present cache at count 2 -- which an exact-equality
precondition would refuse while the other two flags also refuse it (one on
the count, one on the cache). That is the same unnameable-state trap
`--respend-val-look` closes, reopened one state further along.

WHAT A CLEAN-TREE PRECONDITION MEANS FOR A RE-RUN. A crashed `--select`
leaves an untracked `features_norm` manifest; a crashed `--spend-val-look`
leaves an untracked prediction-table manifest. Both make the tree dirty, so
the next invocation refuses. THE RECOVERY IS NOT TO RELAX THE REFUSAL -- it
is to COMMIT the artifact the crashed run already produced (which the
two-commit discipline wants anyway, and which is safe because that manifest
was issued with the clean hash of the invocation that wrote it) and then
re-invoke. `models.slice`'s reuse-before-writing branches at steps 2 and 7
are what make that re-invocation a no-op rather than a `FileExistsError`.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# BEFORE ANY IMPORT THAT CAN REACH NUMBA. Same expression as
# `tests/conftest.py`, so the script path and the pytest path agree.
os.environ.setdefault(
    "NUMBA_CACHE_DIR", str(Path(tempfile.gettempdir()) / "aihf-numba-cache")
)

import argparse  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

from data.lake_paths import (  # noqa: E402
    LAKE_REGISTRY_ROOT,
    lake_root as resolve_lake_root,
    mlflow_tracking_root,
)
from harness.segments import read_segment_manifest  # noqa: E402
from models.cache import (  # noqa: E402
    CACHE_ROOT,
    look_report,
    segment_cache_path,
    tracking_root_digest,
)
from models.frozen import read_frozen_predictor  # noqa: E402
from models.slice import (  # noqa: E402
    MODE_FREEZE,
    MODE_SELECT,
    MODE_VAL,
    VAL_SEGMENT_NAME,
    find_frozen_body,
    run_slice,
)
from models.sweep import selection_path  # noqa: E402
from tracking.mlflow_utils import compute_code_hash  # noqa: E402

#: The approved Phase-7 segment manifest (plan 07-02): `train` days
#: 2026-09-12..16, `val` days 17..18, `budget_allowance = 3`.
APPROVED_SEGMENT_MANIFEST_ID: str = (
    "807125015b252014ad8ee5282a21b2eccbfe01a287122cce5512ec9885aa3548"
)

#: The seed every Phase-7 fit has used (`tests/models/` and `models.sweep`'s
#: own tests). Part of the hashed recipe (D-07-14), so changing it changes
#: every `predictor_id` -- hence a named default rather than a literal at a
#: call site.
DEFAULT_SEED: int = 20260925

#: The shortest `--reason` that can plausibly be a diagnosis rather than a
#: shrug. A second look is one of three irreversible slots; the operator who
#: spends one owes the next reader a sentence.
MIN_REASON_CHARS: int = 40

#: Strings that are a placeholder wearing a reason's clothes.
PLACEHOLDER_REASONS: frozenset[str] = frozenset(
    {
        "",
        "-",
        "?",
        "because",
        "crash",
        "fix",
        "fixme",
        "n/a",
        "na",
        "none",
        "reason",
        "retry",
        "tbd",
        "test",
        "todo",
        "wip",
    }
)


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """`{relative path: (size, mtime_ns)}` for every file under `root`.

    Copied from `scripts/holdout_declare_dry_run_real_lake.py` so a run can
    state exactly which artifacts it created AND that it created nothing
    else. `onerror` absorbs a directory `os.scandir` cannot list (the lockbox
    `chmod 0000` barrier) without this file ever naming it.
    """
    if not root.exists():
        return {}
    entries: dict[str, tuple[int, int]] = {}
    for dirpath, _dirnames, filenames in os.walk(root, onerror=lambda _e: None):
        for name in filenames:
            path = Path(dirpath) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            entries[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns)
    return entries


def _report_diff(label: str, before: dict, after: dict) -> None:
    created = sorted(set(after) - set(before))
    changed = sorted(
        key for key in set(after) & set(before) if after[key] != before[key]
    )
    removed = sorted(set(before) - set(after))
    print(
        f"{label}: {len(created)} created, {len(changed)} changed, {len(removed)} removed"
    )
    for key in created:
        print(f"  + {key}")
    for key in changed:
        print(f"  ~ {key}")
    for key in removed:
        print(f"  - {key}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.run_stage1_slice",
        description=(
            "Run the Stage-1 regression slice. Exactly one mode flag is "
            "required. --spend-val-look and --respend-val-look are the only "
            "flags that can spend the val look."
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--select",
        action="store_true",
        help="steps 1-4: spend the five OOF looks, write selection.json, "
        "write NO registry body",
    )
    mode.add_argument(
        "--freeze",
        action="store_true",
        help="step 5: refit the winner from the train cache and write the "
        "frozen body. Costs no look",
    )
    mode.add_argument(
        "--spend-val-look",
        action="store_true",
        help="steps 6-9: THE ONE HONEST LOOK, behind four refusals",
    )
    mode.add_argument(
        "--resume-from-cache",
        action="store_true",
        help="steps 7-9 from the already-paid-for val cache. Spends nothing",
    )
    mode.add_argument(
        "--respend-val-look",
        action="store_true",
        help="steps 6-9 when the look was spent and the cache was LOST. "
        "Spends a SECOND look; requires --reason",
    )
    parser.add_argument(
        "--reason",
        default=None,
        help=f"the written diagnosis --respend-val-look requires (at least "
        f"{MIN_REASON_CHARS} characters)",
    )
    parser.add_argument(
        "--segment-manifest-id",
        default=APPROVED_SEGMENT_MANIFEST_ID,
        help="default: the approved Phase-7 manifest",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    # THE ROOT OVERRIDES EXIST FOR THE TESTS, and their default is None
    # rather than a resolved path: `lake_root()` mkdirs and write-probes, so
    # as an argparse default it would fire on --help.
    for name in ("lake-root", "registry-root", "tracking-root", "cache-root"):
        parser.add_argument(
            f"--{name}",
            default=None,
            help=f"override the canonical {name.replace('-', ' ')} "
            "(tests only; resolved inside main() when absent)",
        )
    return parser


def _require_reason(reason: str | None) -> str:
    if reason is None:
        raise ValueError(
            "--respend-val-look requires --reason: it can spend a SECOND "
            "irreversible look, and the next reader of this budget is owed "
            "the diagnosis that justified it"
        )
    trimmed = reason.strip()
    if trimmed.lower() in PLACEHOLDER_REASONS or len(trimmed) < MIN_REASON_CHARS:
        raise ValueError(
            f"--reason {reason!r} is a placeholder, not a diagnosis "
            f"(at least {MIN_REASON_CHARS} characters, and not one of "
            f"{sorted(PLACEHOLDER_REASONS)}). State what was lost and how you "
            "know the look was already spent."
        )
    return trimmed


def _foreign_val_caches(
    cache_root: Path, tracking_root: str, segment_manifest_id: str
) -> list[Path]:
    """Every `val.parquet` cached for this manifest under a DIFFERENT tracking
    root's digest.

    A dry-run cache is byte-identical to a paid-for one and cost nothing,
    because `record_look` wrote its run into the scratch store. It cannot be
    READ from here (the digest is a path component -- `models.cache`'s whole
    design), but its presence means somebody has already materialized this
    segment somewhere, and an operator about to spend the real look should be
    told before rather than after.
    """
    mine = tracking_root_digest(tracking_root)
    if not Path(cache_root).is_dir():
        return []
    return [
        path
        for path in sorted(
            Path(cache_root).glob(f"*/{segment_manifest_id}/val.parquet")
        )
        if path.parent.parent.name != mine
    ]


def main(argv: list[str], *, git_runner=subprocess.run) -> int:
    """Parse, refuse, run, and report the look counts at both ends.

    `git_runner` is injectable for the same reason
    `tracking.mlflow_utils.compute_code_hash` makes it injectable: a test
    that called the real git would read `-dirty` under pre-commit (which
    stashes only UNSTAGED changes) and fail hooks 18/19 on every future
    commit in this repo.

    PARSING HAPPENS FIRST AND THE ROOTS ARE RESOLVED AFTER. `--help` and a
    parse error both leave `parse_args` via `SystemExit`, so neither ever
    reaches `lake_root()` -- which would `mkdir` and write-probe the real
    lake as a side effect of printing usage.
    """
    args = _build_parser().parse_args(argv)

    lake_root = Path(args.lake_root) if args.lake_root else resolve_lake_root()
    registry_root = (
        Path(args.registry_root) if args.registry_root else LAKE_REGISTRY_ROOT
    )
    tracking_root = str(
        Path(args.tracking_root) if args.tracking_root else mlflow_tracking_root(None)
    )
    cache_root = Path(args.cache_root) if args.cache_root else CACHE_ROOT
    segment_manifest_id = args.segment_manifest_id
    print(f"lake_root       {lake_root}")
    print(f"registry_root   {registry_root}")
    print(f"tracking_root   {tracking_root}")
    print(f"cache_root      {cache_root}")

    # ---- the one git call, and the refusal every flag shares -------------
    # A `-dirty` hash and a non-empty `git status --porcelain` are the SAME
    # condition: `compute_code_hash` appends the suffix from exactly that
    # command. So this single check is the clean-tree precondition the
    # freeze and the val look both need, and it is stated once.
    code_hash = compute_code_hash(git_runner=git_runner)
    if code_hash.endswith("-dirty"):
        print(
            f"FAIL: the working tree is dirty (code_hash={code_hash}). Every "
            "manifest, look run and frozen body this invocation would write "
            "embeds that string, in append-only registries, forever. Commit "
            "first -- including any artifact a crashed earlier invocation "
            "already produced -- then re-invoke."
        )
        return 1
    print(f"code_hash       {code_hash}")

    manifest = read_segment_manifest(registry_root, segment_manifest_id)
    block_names = [
        entry["name"]
        for entry in sorted(
            (e for e in manifest["segments"] if e["role"] == "oof_block"),
            key=lambda e: e["start_ns"],
        )
    ]
    reported = [VAL_SEGMENT_NAME, *block_names]
    print("--- look_report BEFORE ---")
    before_counts = look_report(
        segment_manifest_id, reported, tracking_root=tracking_root
    )
    val_count = before_counts[VAL_SEGMENT_NAME]
    val_cache = segment_cache_path(
        cache_root, tracking_root, segment_manifest_id, VAL_SEGMENT_NAME
    )
    allowance = int(manifest["budget_allowance"])

    lake_before, registry_before = _snapshot(lake_root), _snapshot(registry_root)
    mode = MODE_SELECT
    respend_reason: str | None = None
    try:
        if args.select:
            mode = MODE_SELECT
        elif args.freeze:
            mode = MODE_FREEZE
            path = selection_path(cache_root, tracking_root, segment_manifest_id)
            if not path.is_file():
                raise ValueError(
                    f"--freeze: no selection.json at {path} -- run --select "
                    "against THIS tracking root first. The winner crosses the "
                    "commit between selecting and freezing in that file and "
                    "nowhere else."
                )
        elif args.spend_val_look:
            mode = MODE_VAL
            _require_frozen_winner_readable(
                cache_root=cache_root,
                tracking_root=tracking_root,
                registry_root=registry_root,
                segment_manifest_id=segment_manifest_id,
                seed=args.seed,
            )
            if val_count != 0:
                raise ValueError(
                    f"--spend-val-look: look_count({VAL_SEGMENT_NAME}) is "
                    f"{val_count}, not 0 -- this segment has already been "
                    "looked at. If the cache is present use "
                    "--resume-from-cache; if it is not, and you can diagnose "
                    "why, use --respend-val-look --reason."
                )
            foreign = _foreign_val_caches(
                cache_root, tracking_root, segment_manifest_id
            )
            if foreign:
                raise ValueError(
                    f"--spend-val-look: a val cache for this manifest already "
                    f"exists under another tracking root's key: {foreign}. A "
                    "dry-run cache must never be mistaken for a paid one; "
                    "delete it or point --tracking-root at the store that "
                    "paid for it."
                )
        elif args.resume_from_cache:
            mode = MODE_VAL
            if not val_cache.is_file():
                raise ValueError(
                    f"--resume-from-cache: no val cache at {val_cache} -- "
                    "there is nothing already paid for to resume from. Use "
                    "--spend-val-look if the count is 0, or "
                    "--respend-val-look --reason if it is not."
                )
            if val_count < 1:
                raise ValueError(
                    f"--resume-from-cache: look_count({VAL_SEGMENT_NAME}) is "
                    f"{val_count} -- nothing has been paid for yet, so there "
                    "is no honest look to resume. Use --spend-val-look."
                )
        else:
            mode = MODE_VAL
            respend_reason = _require_reason(args.reason)
            if val_cache.is_file():
                raise ValueError(
                    f"--respend-val-look: the val cache IS present at "
                    f"{val_cache} -- the look you already paid for has its "
                    "frame. Use --resume-from-cache; a second look would buy "
                    "nothing."
                )
            if val_count < 1:
                raise ValueError(
                    f"--respend-val-look: look_count({VAL_SEGMENT_NAME}) is "
                    f"{val_count} -- no look has been spent, so there is "
                    "nothing to re-spend. Use --spend-val-look."
                )
            if val_count >= allowance:
                raise ValueError(
                    f"--respend-val-look: look_count({VAL_SEGMENT_NAME}) is "
                    f"{val_count} and this manifest's budget_allowance is "
                    f"{allowance} -- the segment is exhausted. The remedy is a "
                    "NEW segment manifest (D-05-14), never another look."
                )
    except ValueError as error:
        print(f"FAIL: {error}")
        print("--- look_report AFTER (nothing was run) ---")
        look_report(segment_manifest_id, reported, tracking_root=tracking_root)
        return 1

    print(f"OK: preconditions satisfied for mode={mode!r}")
    result = run_slice(
        segment_manifest_id,
        mode=mode,
        code_hash=code_hash,
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=tracking_root,
        cache_root=cache_root,
        seed=args.seed,
        respend_reason=respend_reason,
    )
    print(f"OK: mode={mode!r} completed")
    print(f"  normalization_manifest_id  {result.normalization_manifest_id}")
    print(f"  train_end_date             {result.train_end_date}")
    if result.selection_path is not None:
        print(f"  selection.json             {result.selection_path}")
        print(
            f"  winner                     grid {result.winner_grid_index} "
            f"{result.winner_model_class} {dict(result.winner_hyperparameters or {})} "
            f"({result.eligible_count} of {result.n_configs} eligible)"
        )
    if result.predictor_manifest_id is not None:
        print(f"  predictor_manifest_id      {result.predictor_manifest_id}")
        print(f"  predictor_id               {result.predictor_id}")
    if result.run_id is not None:
        print(f"  prediction_table_manifest  {result.prediction_table_manifest_id}")
        print(f"  stage-1-regression run     {result.run_id}")
        print(
            f"  gates                      forecast="
            f"{result.forecast_gate_passed} monetization="
            f"{result.monetization_gate_passed}"
        )
        print(
            "  metrics                    "
            + json.dumps(
                {
                    key: result.metrics[key]
                    for key in sorted(result.metrics or {})
                    if key.startswith(("sim_", "ceiling_", "pnl_", "rank_", "r2_vs_z"))
                },
                sort_keys=True,
            )
        )

    _report_diff("lake", lake_before, _snapshot(lake_root))
    _report_diff("registry", registry_before, _snapshot(registry_root))
    print("--- look_report AFTER ---")
    after_counts = look_report(
        segment_manifest_id, reported, tracking_root=tracking_root
    )
    spent = {
        name: after_counts[name] - before_counts[name]
        for name in reported
        if after_counts[name] != before_counts[name]
    }
    print(f"OK: looks spent by this invocation: {spent or 'none'}")
    return 0


def _require_frozen_winner_readable(
    *,
    cache_root: Path,
    tracking_root: str,
    registry_root: Path,
    segment_manifest_id: str,
    seed: int,
) -> None:
    """The first of `--spend-val-look`'s four refusals: the frozen winner's
    registry body must exist at the committed path and read back cleanly
    through `models.frozen.read_frozen_predictor` -- both hashes verified,
    `predictor_id` re-derived, the design cross-checked.

    Reads the winner out of `selection.json` raw, because the body is looked
    up by the recipe MINUS `code_hash` (the freeze runs at a later commit
    than the select, so the recorded fingerprint is a different string by
    design -- see `models.slice.find_frozen_body`).
    """
    path = selection_path(cache_root, tracking_root, segment_manifest_id)
    if not path.is_file():
        raise ValueError(
            f"--spend-val-look: no selection.json at {path} -- there is no "
            "winner to look at val with. Run --select, then --freeze."
        )
    selection = json.loads(path.read_text())
    winner = selection.get("winner")
    if not winner:
        raise ValueError(
            f"--spend-val-look: {path} records no winner (eligible_count="
            f"{selection.get('eligible_count')}) -- nothing passed the "
            "forecast gate on every OOF block, and the runner-up in a field "
            "where nothing passed has not earned the val look."
        )
    manifest_id, _body = find_frozen_body(
        registry_root=registry_root,
        model_class=str(winner["model_class"]),
        hyperparameters=winner.get("hyperparameters") or {},
        seed=seed,
        normalization_manifest_id=str(selection["normalization_manifest_id"]),
    )
    predictor = read_frozen_predictor(manifest_id, registry_root=registry_root)
    print(
        f"OK: frozen winner {manifest_id[:12]} reads back cleanly "
        f"({predictor.model_class} {dict(predictor.hyperparameters)}, "
        f"predictor_id {predictor.predictor_id[:12]}, "
        f"{predictor.n_rows_fitted} rows fitted)"
    )
    # "AND IT IS COMMITTED" NEEDS NO SECOND GIT CALL. `git status --porcelain`
    # lists an untracked file as `?? path`, so the clean-tree refusal above
    # already implies that this body -- which exists on disk, because
    # `read_frozen_predictor` just read it -- is tracked. A `git ls-files`
    # here would be a second, weaker statement of the same fact, and this
    # script deliberately makes exactly ONE git call per invocation.


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
