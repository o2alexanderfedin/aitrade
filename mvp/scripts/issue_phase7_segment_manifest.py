"""One-shot issuance of Phase 7's `compressed_3seg` segment manifest over the
full seven-day v2 feature pool -- 07-02-PLAN.md Task 2, geometry Option A of
D-07-29: train = 2026-09-12..16, val = 2026-09-17..18.

NEVER COLLECTED BY PYTEST (`scripts/` sits outside `testpaths`). Run it by
hand, ONCE, from `mvp/`, with a clean git tree. It no longer needs a quiet
machine -- the pre-flight measures reclaimable memory against a bar calibrated
from its own peak and refuses below it, naming both numbers:

    export NUMBA_CACHE_DIR=/tmp/nbc
    ./.venv/bin/python3 -m scripts.issue_phase7_segment_manifest

Measured cost, re-measured after the upstream load was projected to the two
columns the derivations read (07-02-PLAN.md Task 1): **30 s wall clock, 4.03 GiB
peak footprint / 3.81 GiB RSS** on a 32 GiB host, against 19.35 GiB / 9.9 GiB /
~5 min for the full-width load 07-RESEARCH.md Q1 measured. Both the memory and
the time came down, because the `.to_list()` loop that dominated the old timing
was paying for a frame that no longer exists at that width. Never `uv run` it --
that holds uv's cache lock for the process's whole life and is the project's ban
on `uv run` for long-lived work.

WHY THIS SCRIPT IS MOSTLY ASSERTIONS. `issue_segment_manifest` has no dry-run
mode: it derives every count from the real partitions and writes the body
itself. D-07-37 asks for "every derived count asserted BEFORE it writes", which
is unachievable exactly as phrased -- the counts come into existence inside the
call. What IS achievable, and what stage (c) below does, is to assert them
immediately after the call and before anything is staged or any look is spent.
While the new body is still untracked, git has no record of it, so a mismatch
is repaired by deleting the file -- `check_manifest_append_only` is anchored on
git history, and `_refuse_overlap_with_exhausted_segments` only arms once a
look exists. After a commit, or after one look against this geometry, neither
is true and the layout is frozen.

Every expected number below was measured in advance (07-RESEARCH.md Q1) by
replaying the identical admission and k-fold functions against the identical
seven v2 partitions, in the identical order. A mismatch means the pool, a
constant, or the code changed -- it is never a licence to edit the expected
value to match. On any mismatch this script deletes the body it just wrote and
exits non-zero.

NO LOOK IS SPENT HERE. This script never materializes a segment. Issuance
reads rows only to derive counts, which is not a validation look; the six
`look_count` values it prints before and after the call must all be 0, and it
prints them precisely so that claim is checkable rather than asserted in prose.

The pre-flight deliberately reads each upstream feature manifest's JSON and
re-checks only its own self-hash, rather than calling
`data.store.resolve_manifest`: resolve re-hashes every partition file on disk,
and the issuance call does exactly that anyway. A second full pass over ~5 GiB
of parquet would cost minutes and prove nothing the call does not.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from data import lake_paths, store
from harness import budget, purge_embargo, row_admission
from harness.segments import issue_segment_manifest
from tracking.mlflow_utils import compute_code_hash

SYMBOL = "BTCUSDT"
STREAM = "features"
DATASET = f"{SYMBOL}.{store.FEATURES_TIER}"

LAYOUT = "compressed_3seg"
K = 5
BUDGET_ALLOWANCE = 3  # D-07-03: one honest val look, two reserved for a bug
CATALOGUE_VERSION = 1  # the ERRATA CATALOGUE version, not the feature schema
FEATURE_SCHEMA_VERSION = 2  # what every upstream v2 feature manifest carries
ERRATA_ID = "22190ad925929001855c4cec56c7ddbd00043d4717ec02ac96fc9b71e3e31b58"

#: The Phase 5 three-day manifest. NOT reused, modified or deleted (D-07-01):
#: it is the only body `segments/` may already hold, and `segments/` holding a
#: second one means a prior attempt of this script left one behind.
EXISTING_3DAY_MANIFEST_ID = (
    "97964cb27f62aa07201e2e52f788240d4f7801582c7cac25ac21107aa9e330e2"
)

#: `(date, v2 feature manifest id, row_count)`, chronological. These are the
#: **v2** ids: the 3-day manifest names the v1 ids for 09-12/13/14, so they
#: must never be copied from it. Each is cross-checked below against the
#: `BTCUSDT.features` by-date pointer rather than trusted as written here.
UPSTREAM_V2: tuple[tuple[str, str, int], ...] = (
    (
        "2026-09-12",
        "91154024ba400fb2d183828f4b6b745d73862b08313cbef33cf5c957503df005",
        4193137,
    ),
    (
        "2026-09-13",
        "5e4ce973b196b60fbdec22bf771ff2c9d517c739d7150cd218a535842eb55f6a",
        6864853,
    ),
    (
        "2026-09-14",
        "1863b8d255d17312e3c6135af19f75e85cd9250d307ed43b8f6a604f95adcbc0",
        11323694,
    ),
    (
        "2026-09-15",
        "2da1b5b97f97b28b1df1500ed91596304d78ba6888900390795721c7bcd2e1a1",
        12203294,
    ),
    (
        "2026-09-16",
        "0bfa8d286d90280251ddabbad340e07f3fd193545de294a3feefb73ec5a63924",
        9872620,
    ),
    (
        "2026-09-17",
        "dcf3b39dad199f1814b3e0b27a506368b50bfabda191bc476a0549da25859fa3",
        8482081,
    ),
    (
        "2026-09-18",
        "d21771bda03691c6c492e6c3fefd6603d09d223e0f5876dc7e2d66a07359948e",
        7986824,
    ),
)
POOL_ROW_COUNT = 60926503
TRAIN_DAY_COUNT = 5  # the first five entries above are `train`; last two `val`

COVERED_START_NS = 1_789_171_200_002_000_000  # 2026-09-12 first etime
COVERED_END_NS = 1_789_775_999_957_000_000  # 2026-09-18 last etime
UTC_MIDNIGHT_09_17 = 1_789_603_200_000_000_000  # == 1789603200 * 10**9

#: One calendar day each, drifting 400 us: `431999998000000 // 5`. Asserted
#: per block, and the span is asserted divisible by K so the last block never
#: absorbs a remainder (`kfold.purged_embargoed_blocks` widens only the last).
EXPECTED_BLOCK_WIDTH_NS = 86_399_999_600_000

#: The values the expected counts below were MEASURED under. The body's own
#: `purge_ns`/`embargo_ns` are asserted against `harness.purge_embargo`'s live
#: constants (so a body cannot understate them), and those constants are in
#: turn asserted against these literals -- a constant that drifts invalidates
#: every count in this file and must fail here rather than silently re-derive.
EXPECTED_PURGE_NS = 600_000_000_000
EXPECTED_EMBARGO_NS = 1_000_000_000
EXPECTED_STALE_BOOK_MAX_AGE_NS = 5_000_000_000

#: name -> (start_ns, end_ns, raw_rows, excluded_stale, excluded_undefined,
#: admitted). `raw_rows` is not a body field: it is the sum of the three
#: mutually-exclusive admission counts, and the sums are what tie back to the
#: upstream manifests' own `row_count`s.
EXPECTED_ENTRIES: dict[str, tuple[int, int, int, int, int, int]] = {
    "train": (COVERED_START_NS, UTC_MIDNIGHT_09_17, 44457598, 152207, 55843, 44249548),
    "val": (UTC_MIDNIGHT_09_17, COVERED_END_NS, 16468904, 174845, 0, 16294059),
    "held_out": (COVERED_END_NS, COVERED_END_NS, 0, 0, 0, 0),
    "oof_block_0": (
        1789171200002000000,
        1789257600001600000,
        4193137,
        221,
        55843,
        4137073,
    ),
    "oof_block_1": (1789257600001600000, 1789344000001200000, 6864853, 0, 0, 6864853),
    "oof_block_2": (
        1789344000001200000,
        1789430400000800000,
        11323695,
        32330,
        0,
        11291365,
    ),
    "oof_block_3": (
        1789430400000800000,
        1789516800000400000,
        12203294,
        61148,
        0,
        12142146,
    ),
    "oof_block_4": (
        1789516800000400000,
        1789603200000000000,
        9872619,
        58508,
        0,
        9814111,
    ),
}
EXPECTED_SEGMENT_ORDER = (
    "train",
    "val",
    "held_out",
    "oof_block_0",
    "oof_block_1",
    "oof_block_2",
    "oof_block_3",
    "oof_block_4",
)
EXPECTED_ROLES = {
    "train": "train",
    "val": "val",
    "held_out": "held_out",
    **{f"oof_block_{i}": "oof_block" for i in range(K)},
}

#: The last 600 s of 2026-09-16 is purged by `val`'s leading edge.
EXPECTED_EFFECTIVE_INTERVALS = {"train": [[1789171200002000000, 1789602600000000000]]}
EXPECTED_PURGED_ROW_COUNT = {"train": 3115}
EXPECTED_EMBARGOED_ROW_COUNT = {"train": 0}
EXPECTED_OOF_TRAINING_ROW_COUNTS = {
    "oof_block_0": 40242038,
    "oof_block_1": 37488354,
    "oof_block_2": 32997430,
    "oof_block_3": 32094158,
    "oof_block_4": 34530706,
}

#: The six budget-bearing segment names (D-05-13's granularity): `val` plus
#: every OOF block. `train` is not a look.
LOOK_SEGMENT_NAMES = ("val", *(f"oof_block_{i}" for i in range(K)))

GIB = 1024**3

#: `/usr/bin/time -l`'s "peak memory footprint" for THIS script, MEASURED on
#: 2026-09-25 against this exact pool with the upstream load projected to
#: `harness.segments.UPSTREAM_DERIVATION_COLUMNS` (07-02-PLAN.md Task 1):
#: 4,331,622,384 B == 4.03 GiB, RSS 3.81 GiB, 30 s wall clock. The full-width
#: load this replaced measured 19.35 GiB / 9.9 GiB / ~5 min and is what made the
#: first attempt at this run refuse.
MEASURED_PEAK_FOOTPRINT_BYTES = 4_331_622_384

#: The bar the pre-flight refuses below: the measured peak times 1.5, rounded up
#: to the next half GiB (4.03 * 1.5 == 6.05 -> 6.5 GiB). The 1.5 is headroom for
#: allocator fragmentation and for whatever the rest of the machine grows into
#: during the 30 s, not a second guess at the peak. Deliberately a bar a machine
#: somebody is WORKING ON can clear -- the old full-width footprint needed ~18
#: GiB free, which in practice meant a machine nobody was allowed to touch, and
#: waiting for one is what stalled this plan for a day.
MEMORY_BAR_BYTES = 13 * GIB // 2

#: `vm_stat`'s page classes that a new allocation can take over without anything
#: being paged out: genuinely free, inactive (clean file cache or reclaimable
#: anonymous), speculative (read-ahead), and purgeable (discardable on demand).
#: NOT `active` and NOT `wired`, which is what makes this a floor on what is
#: available rather than an optimistic reading of total RAM.
#:
#: This reconstructs the measure the previous executor reported (11.48-13.40 GiB
#: across twelve readings) -- 6351293 recorded the numbers but not the formula,
#: and this definition reproduces the same magnitude on the same host, so it is
#: named here to stop the next reader guessing too.
VM_STAT_RECLAIMABLE_CLASSES = (
    "Pages free",
    "Pages inactive",
    "Pages speculative",
    "Pages purgeable",
)


def _reclaimable_memory_bytes() -> int:
    """macOS reclaimable memory, from `vm_stat`, in bytes.

    Raises rather than guessing: an unparseable `vm_stat` (a non-macOS host, a
    changed output format) must refuse the run, not wave it through. A silent
    fallback here would turn the one gate standing between this script and a
    thrashing 32 GiB host into decoration.
    """
    out = subprocess.run(
        ["/usr/bin/vm_stat"], capture_output=True, text=True, check=True
    ).stdout
    page_size = re.search(r"page size of (\d+) bytes", out)
    if not page_size:
        raise RuntimeError(f"vm_stat printed no page size: {out[:200]!r}")
    total_pages = 0
    for label in VM_STAT_RECLAIMABLE_CLASSES:
        match = re.search(rf"^{label}:\s+(\d+)\.", out, re.MULTILINE)
        if not match:
            raise RuntimeError(f"vm_stat printed no {label!r} line: {out[:400]!r}")
        total_pages += int(match.group(1))
    return total_pages * int(page_size.group(1))


FOLD_CONFIG_REASON = (
    "compressed_3seg selected over 5seg although the 7-day pool would support "
    "five segments: a 5seg manifest declares a REAL held_out segment, and "
    "declaring + locking held-out is Phase 8 success criterion 4 (D-07-02). "
    "held_out here is the zero-width sentinel at covered_end_ns (D-07-06). "
    "The inner k=5 purged+embargoed OOF blocks are also what Phase 7's model "
    "selection runs on, never val (D-07-04)."
)

SEGMENTS = [
    {
        "name": "train",
        "role": "train",
        "start_ns": COVERED_START_NS,
        "end_ns": UTC_MIDNIGHT_09_17,
    },
    {
        "name": "val",
        "role": "val",
        "start_ns": UTC_MIDNIGHT_09_17,
        "end_ns": COVERED_END_NS,
    },
    {
        "name": "held_out",
        "role": "held_out",
        "start_ns": COVERED_END_NS,
        "end_ns": COVERED_END_NS,
    },
]


def _ignore_unreadable(_error: OSError) -> None:
    """`os.walk`'s `onerror` hook: a directory `os.scandir` cannot list (the
    `chmod 0000` barrier around the quarantined tier) is a dead end, never a
    raise. This is what keeps that branch unentered without this file ever
    naming it -- the same behaviour `data/lockbox_POLICY.md` documents and
    `scripts/holdout_declare_dry_run_real_lake.py` already relies on."""
    return None


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """`(size, mtime_ns)` per file below `root`."""
    if not root.exists():
        return {}
    entries: dict[str, tuple[int, int]] = {}
    for dirpath, _dirnames, filenames in os.walk(root, onerror=_ignore_unreadable):
        for fname in filenames:
            path = Path(dirpath) / fname
            entries[str(path.relative_to(root))] = (
                path.stat().st_size,
                path.stat().st_mtime_ns,
            )
    return entries


def _check(failures: list[str], label: str, got: object, want: object) -> None:
    """Record a `FAIL:` line naming BOTH numbers when `got != want`."""
    if got == want:
        print(f"  OK: {label}: {got}")
    else:
        failures.append(f"FAIL: {label}: got {got!r}, expected {want!r}")


def _print_look_counts(manifest_id: str, tracking_root: str, label: str) -> list[int]:
    """The six `look_count` values for one manifest. Read-only MLflow query:
    it spends nothing, and every value must be 0."""
    counts = [
        budget.look_count(manifest_id, name, tracking_root=tracking_root)
        for name in LOOK_SEGMENT_NAMES
    ]
    pairs = ", ".join(f"{n}={c}" for n, c in zip(LOOK_SEGMENT_NAMES, counts))
    print(f"  look_count ({label}) {manifest_id[:8]}: {pairs}")
    return counts


def _preflight(
    registry_root: Path, lake_root: Path, tracking_root: str
) -> tuple[str, list[str]]:
    """Stage (a). Returns `(code_hash, failures)`; a non-empty `failures`
    aborts before anything expensive runs."""
    failures: list[str] = []
    print("--- (a) pre-flight ---")

    if not os.environ.get("NUMBA_CACHE_DIR"):
        failures.append(
            "FAIL: NUMBA_CACHE_DIR is unset -- export NUMBA_CACHE_DIR=/tmp/nbc "
            "first, or numba scatters .nbi/.nbc caches inside the repo"
        )

    # Cheapest gate first, and the one this revision exists for: refuse a host
    # that cannot spare the measured peak plus headroom, naming BOTH numbers so
    # the reader can tell a short machine from a broken script.
    reclaimable = _reclaimable_memory_bytes()
    print(
        f"  reclaimable memory (vm_stat free+inactive+speculative+purgeable): "
        f"{reclaimable / GIB:.2f} GiB"
    )
    if reclaimable < MEMORY_BAR_BYTES:
        failures.append(
            f"FAIL: only {reclaimable / GIB:.2f} GiB reclaimable, and this run "
            f"needs {MEMORY_BAR_BYTES / GIB:.2f} GiB -- its measured peak "
            f"footprint is {MEASURED_PEAK_FOOTPRINT_BYTES / GIB:.2f} GiB and the "
            "bar is 1.5x that. Close whatever is holding memory and re-run; "
            "running anyway would thrash rather than finish"
        )
    else:
        print(
            f"  OK: {reclaimable / GIB:.2f} GiB reclaimable clears the "
            f"{MEMORY_BAR_BYTES / GIB:.2f} GiB bar "
            f"(1.5x the measured {MEASURED_PEAK_FOOTPRINT_BYTES / GIB:.2f} GiB peak)"
        )

    # D-07-07: the manifest's code_hash must name a committed tree.
    code_hash = compute_code_hash()
    if code_hash.endswith("-dirty"):
        failures.append(
            f"FAIL: code_hash {code_hash!r} is dirty -- commit or stash first; "
            "D-07-07 exists because the Phase 5 manifest recorded a -dirty hash"
        )
    else:
        print(f"  OK: code_hash is clean: {code_hash}")

    # D-07-01: exactly one pre-existing body, the Phase 5 one.
    existing = sorted(p.name for p in (registry_root / "segments").glob("*.json"))
    expected_existing = [f"{EXISTING_3DAY_MANIFEST_ID}.json"]
    if existing != expected_existing:
        failures.append(
            f"FAIL: registry_root/segments/ holds {existing}, expected exactly "
            f"{expected_existing} -- a second body means a prior attempt of "
            "this script left one behind; inspect and remove it by hand"
        )
    else:
        print("  OK: segments/ holds exactly the one Phase 5 body")

    # Cross-check every upstream id against its by-date pointer rather than
    # trusting the list above, and confirm each is a v2 manifest.
    etime_mins: list[int] = []
    etime_maxs: list[int] = []
    row_counts: list[int] = []
    for date, manifest_id, expected_rows in UPSTREAM_V2:
        pointer_path = store.by_date_index_path(
            registry_root, DATASET, SYMBOL, STREAM, date
        )
        pointed = json.loads(pointer_path.read_text())["manifest_id"]
        if pointed != manifest_id:
            failures.append(
                f"FAIL: by-date pointer for {date} names {pointed}, not the "
                f"hardcoded {manifest_id}"
            )
            continue
        body = json.loads(
            store.manifest_path(registry_root, DATASET, manifest_id).read_text()
        )
        recomputed = store.compute_manifest_id(body)
        if recomputed != manifest_id:
            failures.append(
                f"FAIL: manifest {manifest_id} for {date} re-hashes to "
                f"{recomputed} -- the body was edited"
            )
            continue
        if body.get("schema_version") != FEATURE_SCHEMA_VERSION:
            failures.append(
                f"FAIL: manifest for {date} carries schema_version "
                f"{body.get('schema_version')!r}, not {FEATURE_SCHEMA_VERSION}"
            )
        if body.get("row_count") != expected_rows:
            failures.append(
                f"FAIL: manifest for {date} carries row_count "
                f"{body.get('row_count')!r}, expected {expected_rows}"
            )
        row_counts.append(body.get("row_count", 0))
        for part in body["partitions"]:
            etime_mins.append(part["etime_min"])
            etime_maxs.append(part["etime_max"])
    if not failures:
        print(
            f"  OK: all {len(UPSTREAM_V2)} by-date pointers name the v2 ids, "
            f"schema_version={FEATURE_SCHEMA_VERSION}, row_counts match"
        )

    # The covered bounds this script hardcodes must be what `_covered_range`
    # will read out of those same manifests -- the only place it reads them.
    if etime_mins and etime_maxs:
        _check(
            failures,
            "covered_start_ns (min etime_min)",
            min(etime_mins),
            COVERED_START_NS,
        )
        _check(
            failures, "covered_end_ns (max etime_max)", max(etime_maxs), COVERED_END_NS
        )
    if row_counts:
        _check(failures, "pool row_count", sum(row_counts), POOL_ROW_COUNT)

    # The constants the expected counts were measured under.
    _check(
        failures,
        "purge_embargo.PURGE_HORIZON_NS",
        purge_embargo.PURGE_HORIZON_NS,
        EXPECTED_PURGE_NS,
    )
    _check(
        failures,
        "purge_embargo.FOLD_EMBARGO_NS",
        purge_embargo.FOLD_EMBARGO_NS,
        EXPECTED_EMBARGO_NS,
    )
    _check(
        failures,
        "row_admission.STALE_BOOK_MAX_AGE_NS",
        row_admission.STALE_BOOK_MAX_AGE_NS,
        EXPECTED_STALE_BOOK_MAX_AGE_NS,
    )

    # Pure geometry, checkable before the call.
    span_ns = UTC_MIDNIGHT_09_17 - COVERED_START_NS
    _check(
        failures, "train span divisible by k (no last-block remainder)", span_ns % K, 0
    )
    _check(failures, "train span // k", span_ns // K, EXPECTED_BLOCK_WIDTH_NS)

    # Read-only; spends nothing. All six must be 0 (T-07-07).
    before_looks = _print_look_counts(
        EXISTING_3DAY_MANIFEST_ID, tracking_root, "before, 3-day manifest"
    )
    if any(c != 0 for c in before_looks):
        failures.append(
            f"FAIL: the 3-day manifest already has looks recorded: "
            f"{before_looks} -- expected all 0"
        )

    if lake_root.exists():
        print(f"  OK: lake root present: {lake_root}")
    return code_hash, failures


def _assert_body(body: dict, code_hash: str) -> list[str]:
    """Stage (c). Every derived count, against its pre-measured value."""
    failures: list[str] = []
    print("--- (c) post-issuance assertions on the returned body ---")

    _check(failures, "layout", body["layout"], LAYOUT)
    _check(failures, "budget_allowance", body["budget_allowance"], BUDGET_ALLOWANCE)
    _check(failures, "errata_id", body["errata_id"], ERRATA_ID)
    _check(failures, "version (errata catalogue)", body["version"], CATALOGUE_VERSION)
    _check(failures, "symbol", body["symbol"], SYMBOL)
    _check(failures, "code_hash", body["code_hash"], code_hash)
    if body["code_hash"].endswith("-dirty"):
        failures.append(f"FAIL: body code_hash {body['code_hash']!r} is dirty")
    _check(
        failures, "fold_config_reason", body["fold_config_reason"], FOLD_CONFIG_REASON
    )
    _check(
        failures,
        "upstream_feature_manifest_ids",
        body["upstream_feature_manifest_ids"],
        [mid for _date, mid, _rows in UPSTREAM_V2],
    )

    # The id in the returned dict must be the id of the body under it.
    body_only = {k: v for k, v in body.items() if k != "manifest_id"}
    _check(
        failures,
        "manifest_id == compute_manifest_id(body)",
        store.compute_manifest_id(body_only),
        body["manifest_id"],
    )

    _check(
        failures,
        "segment names, in order",
        tuple(s["name"] for s in body["segments"]),
        EXPECTED_SEGMENT_ORDER,
    )

    counts = body["admission"]["counts"]
    _check(failures, "admission.policy", body["admission"]["policy"], "stale_book")
    _check(
        failures,
        "admission.max_age_ns",
        body["admission"]["max_age_ns"],
        row_admission.STALE_BOOK_MAX_AGE_NS,
    )
    _check(
        failures,
        "admission.exclude_undefined_age",
        body["admission"]["exclude_undefined_age"],
        True,
    )

    for entry in body["segments"]:
        name = entry["name"]
        if name not in EXPECTED_ENTRIES:
            failures.append(f"FAIL: unexpected segment {name!r}")
            continue
        start, end, raw, stale, undef, admitted = EXPECTED_ENTRIES[name]
        _check(failures, f"{name}.role", entry["role"], EXPECTED_ROLES[name])
        _check(failures, f"{name}.start_ns", entry["start_ns"], start)
        _check(failures, f"{name}.end_ns", entry["end_ns"], end)
        got = counts.get(name, {})
        _check(failures, f"{name}.excluded_stale", got.get("excluded_stale"), stale)
        _check(
            failures, f"{name}.excluded_undefined", got.get("excluded_undefined"), undef
        )
        _check(failures, f"{name}.admitted", got.get("admitted"), admitted)
        _check(
            failures,
            f"{name}.raw rows (sum of the three admission counts)",
            sum(
                got.get(key, 0)
                for key in ("admitted", "excluded_stale", "excluded_undefined")
            ),
            raw,
        )
        if entry["role"] == "oof_block":
            _check(
                failures,
                f"{name}.width_ns",
                entry["end_ns"] - entry["start_ns"],
                EXPECTED_BLOCK_WIDTH_NS,
            )

    # Cross-check the geometry against the upstream manifests' own row_counts:
    # `train` is exactly the first five UTC days, and `val` is the last two
    # minus the single row at covered_end_ns that its half-open end excludes.
    train_raw = sum(rows for _d, _m, rows in UPSTREAM_V2[:TRAIN_DAY_COUNT])
    val_raw = sum(rows for _d, _m, rows in UPSTREAM_V2[TRAIN_DAY_COUNT:]) - 1
    _check(
        failures,
        "train raw rows == sum of the first five days' row_count",
        EXPECTED_ENTRIES["train"][2],
        train_raw,
    )
    _check(
        failures,
        "val raw rows == sum of the last two days' row_count - 1",
        EXPECTED_ENTRIES["val"][2],
        val_raw,
    )

    _check(
        failures,
        "purge_ns (from harness.purge_embargo)",
        body["purge_ns"],
        purge_embargo.PURGE_HORIZON_NS,
    )
    _check(
        failures,
        "embargo_ns (from harness.purge_embargo)",
        body["embargo_ns"],
        purge_embargo.FOLD_EMBARGO_NS,
    )
    _check(
        failures,
        "effective_intervals",
        body["effective_intervals"],
        EXPECTED_EFFECTIVE_INTERVALS,
    )
    _check(
        failures,
        "purged_row_count",
        body["purged_row_count"],
        EXPECTED_PURGED_ROW_COUNT,
    )
    _check(
        failures,
        "embargoed_row_count",
        body["embargoed_row_count"],
        EXPECTED_EMBARGOED_ROW_COUNT,
    )
    _check(
        failures,
        "oof_training_row_counts",
        body["oof_training_row_counts"],
        EXPECTED_OOF_TRAINING_ROW_COUNTS,
    )
    return failures


def main(argv: list[str]) -> int:
    if argv:
        print(f"FAIL: this script takes no arguments, got {argv}")
        return 2

    registry_root = lake_paths.LAKE_REGISTRY_ROOT
    lake_root = lake_paths.lake_root()  # mkdirs + write-probes; snapshot AFTER
    tracking_root = str(lake_paths.mlflow_tracking_root())
    print(f"registry root: {registry_root}")
    print(f"lake root:     {lake_root}")
    print(f"tracking root: {tracking_root}")

    code_hash, failures = _preflight(registry_root, lake_root, tracking_root)
    if failures:
        for line in failures:
            print(line)
        print(f"FAIL: {len(failures)} pre-flight check(s) failed -- nothing issued")
        return 1

    before_registry = _snapshot(registry_root)
    before_lake = _snapshot(lake_root)
    print(
        f"  OK: snapshots taken ({len(before_registry)} registry files, "
        f"{len(before_lake)} lake files)"
    )

    print(
        f"--- (b) issuing (measured 30 s, "
        f"{MEASURED_PEAK_FOOTPRINT_BYTES / GIB:.2f} GiB peak; the full-width "
        f"load this replaced needed 19.35 GiB) ---"
    )
    manifest = issue_segment_manifest(
        LAYOUT,
        SEGMENTS,
        upstream_feature_manifest_ids=[mid for _d, mid, _r in UPSTREAM_V2],
        admission={
            "policy": "stale_book",
            "max_age_ns": row_admission.STALE_BOOK_MAX_AGE_NS,
            "exclude_undefined_age": True,
        },
        errata_id=ERRATA_ID,
        budget_allowance=BUDGET_ALLOWANCE,
        fold_config_reason=FOLD_CONFIG_REASON,
        symbol=SYMBOL,
        version=CATALOGUE_VERSION,
        code_hash=code_hash,
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=tracking_root,
        k=K,
    )
    manifest_id = manifest["manifest_id"]
    body_path = registry_root / "segments" / f"{manifest_id}.json"
    print(f"  issued manifest_id: {manifest_id}")
    print(f"  body path:          {body_path}")

    failures = _assert_body(manifest, code_hash)

    print("--- (d) report ---")
    after_registry = _snapshot(registry_root)
    after_lake = _snapshot(lake_root)
    new_registry = sorted(set(after_registry) - set(before_registry))
    new_lake = sorted(set(after_lake) - set(before_lake))
    changed_registry = sorted(
        name
        for name in set(before_registry) & set(after_registry)
        if before_registry[name] != after_registry[name]
    )
    _check(failures, "new registry files", len(new_registry), 1)
    _check(failures, "new lake files", len(new_lake), 0)
    _check(failures, "changed pre-existing registry files", changed_registry, [])
    print(f"  new registry file(s): {new_registry}")

    after_looks = _print_look_counts(
        EXISTING_3DAY_MANIFEST_ID, tracking_root, "after, 3-day manifest"
    )
    new_looks = _print_look_counts(manifest_id, tracking_root, "after, NEW manifest")
    if any(c != 0 for c in after_looks + new_looks):
        failures.append(
            f"FAIL: a look was recorded: 3-day={after_looks} new={new_looks}"
        )

    if failures:
        for line in failures:
            print(line)
        # The body is still UNTRACKED, so deleting it is legal and total.
        # Only ever delete a path that the before-snapshot did not contain.
        relative = str(body_path.relative_to(registry_root))
        if relative in new_registry and relative not in before_registry:
            body_path.unlink()
            print(f"deleted the untracked body {body_path}")
        else:
            print(f"REFUSING to delete {body_path}: not a file this run created")
        print(
            f"FAIL: {len(failures)} assertion(s) failed -- nothing committed. "
            "Do NOT edit an expected value to match; report the disagreement."
        )
        return 1

    print(
        f"OK: issued {manifest_id} -- every derived count matches its "
        f"pre-measured value, 1 new registry file, 0 new lake files, "
        f"0 looks spent, code_hash {code_hash} clean."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
