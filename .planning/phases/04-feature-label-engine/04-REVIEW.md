---
phase: 04-feature-label-engine
reviewed: 2026-09-19T12:16:43Z
depth: deep
files_reviewed: 29
files_reviewed_list:
  - mvp/features/kernel.py
  - mvp/features/reference.py
  - mvp/features/event_stream.py
  - mvp/features/labels.py
  - mvp/features/tier.py
  - mvp/features/build.py
  - mvp/features/api.py
  - mvp/features/normalize.py
  - mvp/features/__init__.py
  - mvp/data/time_ns.py
  - mvp/data/holdout.py
  - mvp/data/store.py
  - mvp/data/dq/checks.py
  - mvp/data/dq/feature_checks.py
  - mvp/data/dq/report.py
  - mvp/spec/information_set.py
  - mvp/spec/features.toml
  - mvp/spec/labels.toml
  - mvp/spec/dq_thresholds.toml
  - mvp/spec.md
  - mvp/tools/check_single_feature_path.py
  - mvp/tools/check_ms_to_ns_site.py
  - mvp/tests/leakage/test_feature_information_set.py
  - mvp/tests/leakage/test_label_information_set.py
  - mvp/tests/leakage/test_embargo.py
  - mvp/tests/fixtures/feature_build.py
  - mvp/tests/tools/test_ci_pre_commit_parity.py
  - .github/workflows/ci.yml
  - .pre-commit-config.yaml
findings:
  critical: 1
  warning: 7
  info: 6
  total: 14
status: issues_found
---

# Phase 4: Code Review Report

**Reviewed:** 2026-09-19
**Depth:** deep (cross-file, with runtime reproductions against a scratch lake and read-only
measurements against the real lake)
**Files Reviewed:** 29
**Status:** issues_found

## Summary

The feature engine holds up where it is load-bearing. I mutated the kernel to give `mid` a one-row
lookahead and mutated the label as-of rule from prevailing to next-quote; the leakage suite went
red both times (5 and 4 failures respectively), naming the offending rows. The suite is a gate, not
a claim, and it is wired into both CI and pre-commit under its own step name. All six guardrail
tools pass on this branch. The kernel and its pure-Python reference are line-for-line mirrors with
no drift I could find, and the reference's unbounded deque genuinely shares no representation with
the ring it checks.

**The one thing that breaks is not in the arithmetic — it is in who is allowed to read the result.**
Day D's feature partition carries day D+1's prices in its label tail, by design. The build refuses
to *create* such a partition when D+1 is held out. Nothing refuses to *read* one that already
exists. Phase 5 has not chosen the held-out window yet, so this is still cheap to fix; the moment
it does, three partitions already on the lake become a channel out of the lockbox.

Seven warnings follow, of which two have teeth: a single crash between the partition write and its
manifest permanently wedges that date and aborts the rest of the daily range, and the label gap
threshold is a single absolute 30 s applied to horizons of 1 s and 10 s, so a silence three times
the primary horizon still produces a finite label of exactly zero.

---

## Critical Issues

### CR-01: Day D's feature partition hands out day D+1's prices, and the reader never asks whether D+1 is held out

**File:** `mvp/features/tier.py:376-381` (`load_features`), with
`mvp/features/tier.py:246-264` (`curated_manifest_input`) and
`mvp/features/build.py:457-482` (`issue_feature_manifest` call)

**Issue:**

D-04-11 says the lockbox must not be laundered through a neighbouring day's label tail, and
`assert_buildable` enforces exactly that — on the **build** side, for a day not yet built.
`load_features` derives the dates it refuses from the manifest's own partitions only:

```python
dates = sorted({part["date"] for part in manifest["partitions"]})
assert_not_quarantined(dates, symbol=manifest["symbol"], ...)
```

A features partition for D has exactly one partition date, D. The `l1_label_tail` input — the
curated D+1 manifest whose prevailing mids became D's last ten minutes of `ret_10min_mid`,
`ret_1min_mid` and `ret_10s_mid` — is recorded in `inputs[]` with its role, but its **dates are
not recorded at all** (`curated_manifest_input` writes `path`, `sha256`, `rows`, `dataset`,
`manifest_id`, `role`), so there is nothing for the read-time gate to compare even if it wanted to.

The stored data is not a summary of D+1; it is D+1's price path. `mid_t` is stored raw and the
label is a ratio, so `mid_{t+h} = mid_t * (1 + ret)` recovers the held-out quote exactly.

**Reproduction 1 — real bytes already on the lake** (read-only):

```
$ cd /Volumes/ProjectsSSD/aihedgefund/repo/mvp
$ PYTHONPATH=$PWD ./.venv/bin/python3 <<'EOF'
  # read lake/features/.../date=2026-09-14 and reconstruct mid_t*(1+ret_10min_mid),
  # then compare against the prevailing mid in curated bookTicker date=2026-09-15
EOF
decision rows in 09-14's final 10 min with a non-null ret_10min_mid: 77962
of those, reconstructed target lands INSIDE 2026-09-15: 77962
max |reconstructed 09-15 mid - actual 09-15 prevailing mid| = 1.4551915228366852e-11
distinct 09-15 mid values recovered: 243  price range: 78090.35 -> 78222.45
time span of 09-15 recovered: 599.991 s from 2026-09-15T00:00:00.008000000
```

The whole first 600 seconds of 2026-09-15's mid path, at 77,962 sample points, exact to 1.5e-11
USDT, sitting inside a partition whose own date is 2026-09-14.

**Reproduction 2 — the gate does not fire** (scratch lake, `mktemp -d`, fixtures from
`tests/fixtures/feature_build.py`):

```
$ PYTHONPATH=$PWD NUMBA_CACHE_DIR=/tmp/nbcache ./.venv/bin/python3 probeB2.py
load_features(2026-09-13) SUCCEEDED while 2026-09-14 is held out; rows = 86500
shape: (3, 3)
┌─────────────────────┬───────┬───────────────┐
│ etime               ┆ mid   ┆ ret_10min_mid │
╞═════════════════════╪═══════╪═══════════════╡
│ 1789343997000000000 ┆ 104.7 ┆ 0.0           │
│ 1789343998000000000 ┆ 104.8 ┆ 0.0           │
│ 1789343999000000000 ┆ 104.9 ┆ 0.0           │
└─────────────────────┴───────┴───────────────┘
held-out mids recovered: [104.7, 104.8, 104.9]
```

The control confirms the gate itself works for the case it covers: holding out D *itself* raises
`QuarantinedDateError`. Only the tail is unguarded.

**Why the existing residual does not cover this.** T-04-09 (04-02-SUMMARY, 04-05-SUMMARY) says "a
features partition written before its date is declared held out stays readable on disk" and hands
Phase 5 the instruction to *move or delete the partition for the declared date*. That instruction
would have Phase 5 move `features/date=2026-09-15` — which does not exist — and leave
`features/date=2026-09-14` in place, which is the one that carries the data. The affected partition
is the one for the day *before* the declared date, and no artifact or handoff note points at it.
T-04-18 is explicitly build-side ("this plan's build path calls it before any load").

**Fix:**

Derive the tail date at read time rather than recording it. The tail is *always* exactly D+1 —
`next_day_quote_series` builds it from `next_utc_date(date)` and there is no other path into it —
so `load_features` can compute the set it must refuse on without touching a single manifest:

```python
# features/tier.py -- load_features, replacing the partitions-only derivation
dates = {part["date"] for part in manifest["partitions"]}
dates_with_tail = sorted(dates | {_next_utc_date(d) for d in dates})
assert_not_quarantined(
    dates_with_tail,
    symbol=manifest["symbol"],
    registry_root=registry_root,
    context=(
        f"load_features of manifest {manifest_id[:12]} "
        "(its long-horizon label tail reads the NEXT day's quotes)"
    ),
)
```

This covers the three partitions already on the lake with no reissue, and it is fail-closed by
construction: a day is refused whenever either the day or its successor is held out, whether or
not anyone remembered to record the dependency.

**Do not make the refusal depend on a new manifest field.** Manifest bodies are content-hashed
(`manifest_id = sha256(body)`) and `check_manifest_append_only` / `check_no_manifest_rewrite` are
CI gates, so the three existing bodies cannot be backfilled — a read-time gate that consults
`inputs[].dates` would fail *open* on exactly the partitions that matter. Recording
`inputs[].dates` in `curated_manifest_input` is still worth doing as provenance (it makes the
dependency legible in the artifact), but it must not be the thing the gate reads.

`next_utc_date` lives in `features/labels.py`, which already imports `features/tier.py`, so
importing it the other way is circular — lift it to a neutral module (`data/time_ns.py` has no
`datetime` dependency today; a two-line `data/dates.py` is the cleaner home) rather than
duplicating the calendar arithmetic.

**The cost, stated plainly, because Phase 5 hits it on day one.** With this fix, declaring
2026-09-15 held out also refuses 2026-09-14 wholesale, leaving two trainable days of four — and
one of those (09-12) is the partial-L1 day. The alternative is a read-time null of the label
columns for rows with `t + h >= holdout_start`, which preserves 09-14's features. I do not
recommend it: it makes the loader a transform rather than a verified read, and the day's DQ
statistics (`null_primary_label_rows`, the zero fractions) would stop describing the bytes on
disk. Refusing the day is the honest version; buying back the rows is a fold-design decision for
Phase 5, taken deliberately.

**What this finding is and is not.** `embargo >= horizon` already keeps those tail rows out of a
*training* fold, so this is not "a model will be trained on held-out prices". It is the narrower
and still-disqualifying claim D-04-11 makes: a readable tier hands back a near-lossless transform
of exactly the bytes the lockbox withholds, to anyone who calls the sanctioned loader.

---

## Warnings

### WR-01: One crash between the partition write and its manifest wedges that date, and aborts every later date in the range

**File:** `mvp/features/build.py:425-485` (steps 8-10), `mvp/features/tier.py:220-226`
(write-once refusal), `mvp/features/build.py:540-566` (`build_features_range`'s except clause)

**Issue:** `write_feature_partition` (step 8) writes a complete, atomically-renamed parquet file.
The by-date pointer is only written at step 10, by `issue_feature_manifest`. Anything in between —
`_build_stats`'s own `AssertionError` on a missing key, a full disk on `build_stats.json`, a
SIGKILL — leaves a valid-looking orphan part file with no manifest. `_already_built` consults the
pointer, sees nothing, and retries; `write_feature_partition`'s write-once glob then refuses
forever. `build_features_range` catches only `NextDayUnavailableError`, so the `FileExistsError`
propagates and the remaining dates in the range are never attempted.

The 04-05 handoff note says "`build_features_range` is idempotent and safe to re-run daily". After
one mid-build crash that is false, and the repair requires a human to delete a file from a
write-once tier — the operation the tier exists to forbid.

**Reproduction** (scratch lake; `_build_stats` patched to raise, simulating a crash after the write):

```
$ PYTHONPATH=$PWD NUMBA_CACHE_DIR=/tmp/nbcache ./.venv/bin/python3 probeC.py
build 1 died after the write: simulated crash after the write
orphan partition left on disk: ['part-1789819727680105000.parquet']
features by-date pointer exists: False

-- retry, exactly as the daily cron would (build_features_range) --
RANGE ABORTED: FileExistsError feature partition .../features/symbol=BTCUSDT/date=2026-09-13
already has a written part file: .../part-1789819727680105000.parquet -- partitions are write-once
```

**Fix:** make the orphan self-identifying and recoverable without touching a manifested file.
Write the partition under a `.building-` prefix and rename it into `part-` only after the manifest
is issued; or, minimally, teach the write-once refusal to distinguish a manifested part file from
an unmanifested one and let the range loop report the date as `"orphaned"` rather than aborting:

```python
# features/build.py -- build_features_range
except NextDayUnavailableError as exc:
    ...
except FileExistsError as exc:
    results.append({
        "date": date, "status": "orphaned", "manifest_id": None,
        "reason": "a part file exists with no manifest -- a previous build died "
                  "between the write and the manifest; the file must be removed "
                  "before this date can be rebuilt",
        "detail": str(exc),
    })
```

The later dates must still be attempted: one wedged day silently truncating a week of builds is the
failure that looks most like success.

---

### WR-02: One absolute 30 s gap threshold serves all four horizons, so a silence three times the primary horizon still yields a finite label of exactly zero

**File:** `mvp/features/labels.py:316-330`, `mvp/spec/dq_thresholds.toml:241-257`

**Issue:** D-04-05 says a row whose horizon window spans a gap gets "a **null** label — never a
zero, never a carried-forward price". The implementation reads "spans a gap" as "spans a gap longer
than `label_gap.max_quote_gap_seconds = 30`", one absolute number for every horizon. That is 3× the
primary label's horizon and 30× `ret_1s_mid`'s. A silence shorter than 30 s that entirely contains
`[t, t+h]` leaves `searchsorted(..., "right") - 1` pointing at the same quote that produced `mid_t`,
so the label is exactly `0.0` — finite, non-null, no null reason, and indistinguishable from a real
measurement of "the market did not move".

**Reproduction (mechanism):**

```
$ PYTHONPATH=$PWD ./.venv/bin/python3 probeA.py
ret_10s_mid = [0.]  ret_1s_mid = [0.]
null counts 10s: {'null_total': 0, 'null_no_mid': 0, 'null_past_end': 0, 'null_gap': 0}
n_big_quote_gaps: 0
true move over the silence: 100.0 -> 137.0  (+37%)
```

A 25 s L1 silence, the price moves 37 % across it, and the label says zero.

**Reproduction (real lake, read-only):**

```
2026-09-12 gaps in (1s,10s]: 29   (10s,30s]: 2   (30s,+]: 2
2026-09-13 gaps in (1s,10s]: 27   (10s,30s]: 0   (30s,+]: 0
2026-09-14 gaps in (1s,10s]: 0    (10s,30s]: 0   (30s,+]: 4

silence  24.155s  mid 77288.1 -> 77288.1 (+0.000%)  rows fully inside: 38  ret_10s_mid==0.0: 38  null: 0  warmup-flagged: 0
silence  19.666s  mid 77140.8 -> 77139.6 (-0.001%)  rows fully inside: 31  ret_10s_mid==0.0: 31  null: 0  warmup-flagged: 0
TOTAL spurious exact-zero primary labels from sub-threshold silences on 2026-09-12: 69
```

**Honest impact today: small.** 69 primary labels on 09-12, and on both silences the venue price
genuinely did not move, so the fabricated zeros happen to be right. `ret_1s_mid` inherits all 56
sub-threshold silences across 09-12/09-13 and is diagnostic-only. The reason this is a warning and
not an Info is that the harm is unbounded in the other direction: the mechanism guarantees a
"no move" label across any silence up to 30 s, and outage lengths are not the pipeline's to choose.
Note also that neither `warmup` nor `post_gap_warmup` flags these rows, so no consumer-side filter
removes them.

**Fix (preferred): null when no quote arrived in `(t, t+h]` at all.** That criterion *is* "never
a carried-forward price", stated exactly, and it needs no threshold:

```python
# features/labels.py -- inside the per-horizon loop
idx_t = np.searchsorted(quote_etime, decision_etime, side="right") - 1
null_stale = (idx <= idx_t) & ~null_no_mid & ~null_past_end   # idx is the t+h index
absent = null_no_mid | null_past_end | null_gap | null_stale
```

One extra `searchsorted` per horizon, horizon-independent, and it nulls almost nothing on a dense
day. Verified against both the failing case and the case a cruder fix would over-null:

```
probeA (25 s silence swallowing a 10 s window)        -> null? [ True]   # correctly nulled
5 s gap ending at t+0.5 s, h=1 s, a quote DID arrive  -> null? [False]   # correctly kept
dense day (5 quotes/s), h=10 s                        -> null? [False False]
```

The cruder alternative, `effective_threshold = min(threshold_ns, horizon_ns)` fed to
`big_quote_gaps` per horizon, also removes the fabricated zeros but over-nulls: a 5 s gap that ends
half a second into a 1 s window kills a label that had a genuine quote in it.

Whichever way this is resolved, it moves the zero fractions D-04-17 records (43.9 % / 80.8 %) and
the numbers pinned in `spec/labels.toml`'s notes, so it is a catalogue-touching change, not a
silent one. If the decision is to keep one absolute threshold, say so in the notes: today they
promise "never a carried-forward price" and the code does not deliver that for h < 30 s.

---

### WR-03: `load_normalization` has no holdout refusal, and the artifact records no dates to refuse on

**File:** `mvp/features/normalize.py:398-460` (`load_normalization`),
`mvp/features/normalize.py:322-396` (`write_normalization_artifact`)

**Issue:** The same shape as CR-01, one tier over. `load_normalization` runs `resolve_manifest` and
`read_verified_partitions` and nothing else — no `assert_not_quarantined`. The artifact body
carries `train_end_date`, `train_etime_min/max` and `source_feature_manifest_ids`, but no list of
the dates the parameters were fit over, so nothing at read time could decide the question anyway.
A mean and a standard deviation are a weaker channel than a price path, but they are still a
summary of whatever rows went in.

The real artifact on the lake today is clean — `train_end=2026-09-13`, sourced from the 09-12 and
09-13 feature manifests — so this is a latent gap, not a live breach.

The docstring's justification for skipping `_enforce_dq_pause` is correct and should stay; it says
nothing about the holdout gate, which is a different question and does apply.

**Fix:** add `train_dates` to `NORMALIZATION_ARTIFACT_SCHEMA` (a `pl.List(pl.Utf8)` beside
`source_feature_manifest_ids`), populate it from the source feature manifests' partition dates, and
in `load_normalization`:

```python
assert_not_quarantined(
    sorted(set(first["train_dates"])),
    symbol=first["symbol"],
    registry_root=registry_root,
    context=f"load_normalization of {manifest_id[:12]}",
)
```

---

### WR-04: A missing resync sidecar silently bakes `post_gap_warmup = false` into a write-once partition, and no recorded number can tell that from "there was no outage"

**File:** `mvp/features/build.py:148-186` (`_post_gap_warmup_tags`),
`mvp/features/build.py:244` (`post_gap_warmup_rows` in `_build_stats`)

**Issue:** The function returns an all-false array when `dq/date=<D>/resync_windows.parquet` does
not exist, and the docstring defends it: *"the report is regenerable, the partition is not"*. That
reasoning runs backwards. Precisely **because** the partition is not regenerable, a build that runs
before the DQ report exists writes an un-fixable column: every row of a multi-minute post-outage
warm-up carries `post_gap_warmup = false` forever, and the only repair is a new manifest for a
day that was already issued.

The absence is also invisible afterwards. `build_stats.json` records
`post_gap_warmup_rows: 0`, which is exactly what a clean day records. Nothing in
`FEATURE_BUILD_STATS_KEYS` distinguishes "the sidecar said there were no windows" from "there was
no sidecar", so `check_feature_warmup` reports `ok` either way.

The partitions currently on the lake were built with sidecars present (09-14 tagged 20,786 rows),
so no data is wrong today — but the sidecars were also *rewritten* after the partitions were
written (`dq/date=2026-09-14` 03:07:23 vs `features/date=2026-09-14` 03:06:16), which is precisely
the ordering that makes the invariant unenforced rather than merely unbroken.

**Fix:** record the fact, and let the DQ report judge it.

```python
# features/build.py
def _post_gap_warmup_tags(decision_etime, lake_root, date) -> tuple[np.ndarray, bool]:
    path = dq_resync_windows_path(Path(lake_root), date)
    tags = np.zeros(decision_etime.shape, dtype=np.bool_)
    if not path.exists():
        return tags, False          # <- sidecar_present
    ...
    return tags, True

# _build_stats
"resync_sidecar_present": bool(sidecar_present),
```

Add `resync_sidecar_present` to `FEATURE_BUILD_STATS_KEYS` and have `check_feature_warmup` return
`degraded` when it is `False`. A degraded day is acknowledgeable; a silently-untagged one is not.

---

### WR-05: `write_normalization_artifact`'s write-once refusal is unreachable

**File:** `mvp/features/normalize.py:369-375`, with `mvp/features/normalize.py:304-319`

**Issue:** `normalization_artifact_path` embeds `time.time_ns()` in the filename, so the
`if final_path.exists()` guard on the very next line tests a path that was constructed
nanoseconds earlier and cannot exist. The refusal is dead code; its sibling one tier over
(`features/tier.py:220`) globs the parent directory for `part-*.parquet`, which is what actually
enforces the rule.

**Reproduction:**

```
$ PYTHONPATH=$PWD ./.venv/bin/python3 probeD.py
second write_normalization_artifact did NOT raise; part files now:
  ['part-1789819771896022000.parquet', 'part-1789819771903611000.parquet']
two distinct manifests: bf37eb12e5a4 2d866961e89b
```

Two artifacts for the same `(symbol, train_end)`, each with its own manifest. Nothing is corrupted
— each manifest names its own bytes — but the guard reads as protection that is not there, and a
reader comparing the two tiers will conclude the norm tier is write-once when it is not.

**Fix:** either mirror the features tier, or delete the guard and say in the docstring that several
artifacts may share a `train_end=` directory (different fold, different feature set) and that the
manifest is the identity.

```python
existing = sorted(final_path.parent.glob("part-*.parquet"))
if existing:
    raise FileExistsError(
        f"normalization artifact directory {final_path.parent} already holds "
        f"{existing[0]} -- partitions are write-once"
    )
```

---

### WR-06: A corrupt feature partition now takes down the whole DQ report for that date — curated rows included — and stops the report loop

**File:** `mvp/data/dq/report.py:419-425` (`build_feature_report_rows_for_date`),
called unguarded from `mvp/data/dq/report.py:368-378`; loop at `mvp/data/dq/report.py:612-621`

**Issue:** `build_feature_report_rows_for_date` calls `resolve_manifest(..., expected_tier=
FEATURES_TIER)` with no `try`/`except`. A `ManifestHashMismatch` on a feature partition therefore
propagates out of `build_report_rows_for_date`, out of `write_report`, and out of `main`'s date
loop — so the date's `report.parquet` is never rewritten *and* every later date in a `--range`
run is skipped.

The curated side raises on the same condition, so this is not a new class of failure. What is new
is the direction of the dependency: before this branch, the curated DQ report depended only on
curated integrity. Now a downstream, fully-rebuildable tier can block the refresh of the upstream
tier's verdict — and the stale `report.parquet` left behind keeps vouching for curated manifests,
so `load_curated` carries on undisturbed while the operator sees a traceback naming `features`.

**Fix:** treat a features-manifest resolution failure as one `failed` row, which is the shape this
module already uses for `feature_build_stats`:

```python
# report.py needs `from data.store import ManifestHashMismatch, ManifestTierError`
try:
    manifest = resolve_manifest(..., expected_tier=FEATURES_TIER)
except (ManifestHashMismatch, ManifestTierError) as exc:
    return [{
        "date": date, "symbol": symbol, "stream": FEATURES_TIER,
        "check": "feature_manifest", "dq_status": "failed",
        "reason": f"features manifest for {date} does not resolve: {exc}",
        "manifest_id": None,
    }]
```

That keeps the verdict fail-closed (a `failed` row pauses `load_features`) while letting the
curated half of the report — and the rest of the range — regenerate.

---

### WR-07: The holdout registry declares a version and never checks it

**File:** `mvp/data/holdout.py:52` (`HOLDOUT_REGISTRY_VERSION = 1`),
`mvp/data/holdout.py:128-147` (`_registry_problem`)

**Issue:** The module's whole thesis is "fail-closed is the only safe direction", and
`_registry_problem` is thorough: not-an-object, no `dates` key, a non-list, a malformed date, the
wrong symbol. It never looks at `version`. A future v2 document with a different shape — per-symbol
maps, ranges instead of dates, an `exclusions` key — would be read by the v1 parser, and any date
it failed to interpret would silently un-hold.

**Reproduction:**

```
$ PYTHONPATH=$PWD ./.venv/bin/python3 probeD.py
registry declaring version=99 accepted as v1: {'2026-09-15'} declared: True
```

**Fix:**

```python
if body.get("version") != HOLDOUT_REGISTRY_VERSION:
    return (
        f"holdout registry version {body.get('version')!r} is not "
        f"{HOLDOUT_REGISTRY_VERSION} -- refusing to read a document this "
        "parser was not written for"
    )
```

---

## Info

### IN-01: `_frame`'s `expanding=True` branch is unreachable

**File:** `mvp/features/api.py:251-292`, call sites at `:320` and `:415`

Both call sites pass `expanding=False`; `for_training`'s fit path bypasses `_frame`'s normalization
entirely and applies `expanding_z` itself at `:366-372`. The `expanding` parameter and the
`expanding_z(columns[name])` branch are dead. Given the module's own 04-05-style practice of
deleting branches proved dead by mutation, this one should go the same way: drop the parameter, and
let `for_training` keep owning the expanding transform.

### IN-02: `for_training`'s `fit_normalization` parameter shadows the imported function of the same name

**File:** `mvp/features/api.py:65` (import) and `:330` (parameter)

The docstring warns about it at `:336-341` and routes the fit through
`fit_normalization_from_frame` to escape the shadow. That is a correct workaround for a hazard that
does not need to exist. Renaming the parameter to `fit=True` (or importing the function as
`_fit_welford`) removes the trap instead of documenting it; as written, any future line added
inside `for_training` that calls `fit_normalization(...)` calls a `bool`.

### IN-03: `PRIMARY_LABEL` is defined twice, by two different derivations

**File:** `mvp/features/labels.py:104` (`get_label("ret_10s_mid").name`) and
`mvp/features/tier.py:112` (`LABEL_COLUMNS[0]`)

Both resolve to the same string today and both are reached through the catalogue, so neither can
drift toward an uncatalogued name. They can still drift from *each other*: reordering
`LABEL_COLUMNS` changes one and not the other, and `labels.py`'s docstring explicitly declines to
import from `tier.py`. One assertion in either module (`assert tier.PRIMARY_LABEL ==
labels.PRIMARY_LABEL`) or a test would close it.

### IN-04: `write_feature_partition`'s write-once check is a time-of-check/time-of-use glob

**File:** `mvp/features/tier.py:220-226`

Two concurrent builds of the same date both glob an empty directory, both write distinct
`part-<ns>.parquet` files, both issue manifests, and the second overwrites the by-date pointer. A
third build then sees two files and refuses. The daily cron is single-process so this is not a live
risk, but the refusal reads as an invariant and is not one. An `O_EXCL` lock file in the `date=`
directory, or writing under a reserved name before the glob, makes it one.

### IN-05: `LabelGapThresholds.max_quote_gap_ns` rounds a float multiply

**File:** `mvp/data/dq/checks.py`, `max_quote_gap_ns` property

`int(self.max_quote_gap_seconds * NS_PER_SECOND)` is exact for the integral `30` in the TOML today.
It truncates for a fractional value — `int(2.3 * 1_000_000_000)` is `2299999999`, one nanosecond
short. Given the project's stated aversion to float time arithmetic,
`round(Decimal(str(seconds)) * NS_PER_SECOND)` or a documented integer-milliseconds key would match
the rest of the codebase's discipline.

### IN-06: `for_simulation` silently truncates a non-int64 `etime` from a live feed

**File:** `mvp/features/api.py:_simulate` / `_row_dtype`

`buffers[name][0] = event[name]` assigns into a preallocated int64 array, so a feed handing
`etime` as a Python float (or a float64 numpy scalar) is truncated with no error. Replayed streams
come from `event_row_stream`, which yields int64 scalars, so this is latent. `EVENT_SCHEMA` is
already the contract everywhere else in the phase; one dtype check on the first row of the stream
would make the simulator hold to it too.

---

## What I checked and did not find a defect in

Stated so a green area is not mistaken for an unexamined one.

- **Kernel vs reference drift.** Statement-for-statement mirrors, including the four OFI additions
  in contract order and the `float(int)/float(int)` emission. The `round()` half-even question is
  moot: if the two ever rounded differently, both would fail the identical round-trip check and
  refuse, so there is no silent path.
- **Ring-buffer bounds.** I tested the case I expected to be a false positive (insert before
  eviction) with a cap-8 ring and 8 well-separated trades: `max_occupancy = 1`, no refusal. The
  eviction advances `head`, so the limit binds only on simultaneously-live entries. The
  power-of-two check, the array-length check and the "never wrap" refusal are all present and
  correct.
- **int64 overflow in `acc_scaled`.** 65,535 live entries at a plausible BTC quantity scaled by
  1e8 is ~1e16, three orders under the int64 bound.
- **Leakage suite is load-bearing, not decorative.** Two mutations in a scratch copy of the tree:
  a one-row lookahead in `mid` → 5 failures including `test_feature_measured_lookback_matches_the_declaration`;
  the as-of rule flipped to next-quote → 4 failures including
  `test_the_value_reach_is_strictly_narrower_than_the_mask_reach`. Anti-vacuity assertions are
  present on both sides, so a constant-returning stub cannot pass.
- **CI wiring.** The leakage suite has its own named step in both `.pre-commit-config.yaml` and
  `.github/workflows/ci.yml`, byte-identical, and `tests/tools/test_ci_pre_commit_parity.py`
  enforces the pre-commit ⊆ CI direction. `pytest tests/leakage` on an empty directory exits 5,
  which CI treats as failure, so the gate cannot go vacuous.
- **All six guardrail tools pass on this branch** (`check_single_feature_path`,
  `check_ms_to_ns_site`, `check_numba_globals`, `check_lockbox_containment`, `check_spec_diff`,
  `check_catalogue_completeness`).
- **`BY_DATE_INDEXED_TIERS` does not weaken 03-REVIEW CR-04.** The quarantined tier is not a
  member, and the `<SYMBOL>.features` dataset namespace is disjoint from the curated ones, so no
  manifest can re-point a pointer another loader follows.
- **`_gap_overlaps`'s single-searchsorted shortcut is exact,** not an approximation: gap starts and
  ends are both derived from a non-decreasing `etime` and the intervals are adjacent pairs, so
  `gap_ends` ascends and the last gap starting before `t+h` has the largest end.
- **Declared residuals not re-reported:** the `post_gap_warmup` / `null_gap` asymmetry (04-05), the
  `resync_warmup = 60 s` left-edge error (04-05), T-04-09 for a partition's own date,
  `check_single_feature_path` sanctioning `features/` wholesale (04-06), and
  `build_stats["window_overflow"]` always being `False` (04-05).

## What was NOT done

- **I did not rebuild any real partition or re-run the full test suite.** `tests/leakage` (23
  passed) and the six guardrail tools were run against the real tree; everything else was
  reproduced in `mktemp -d` scratch lakes or in a copied source tree. The lake,
  `mvp/data/lake_registry/` and `capture/` were read-only throughout, and nothing in the repo was
  modified except this file.
- **I did not quantify WR-02 across the whole lake at every horizon.** I measured the primary
  label's exposure on the only day that has sub-threshold silences long enough to swallow a 10 s
  window (09-12: 69 rows). `ret_1s_mid`'s exposure across all 56 silences over 1 s on 09-12/09-13
  is bounded by the same mechanism but not counted.
- **I did not audit the 40+ new test files line by line.** I read the leakage suite, the build
  fixtures and the CI-parity test in full, and mutation-tested the leakage suite. The remaining
  test modules were read only where a finding pointed at them.
- **I did not check whether Phase 5's fold harness will actually call `load_features`** rather than
  reading `lake/features/` directly. CR-01's fix protects the loader; it does not protect a reader
  that bypasses it, which is the same residual T-04-09 already accepts for the byte-level case.

---

_Reviewed: 2026-09-19_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
