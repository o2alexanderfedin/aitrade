# Phase 7: Regression Track & Vertical Slice - Pattern Map

**Mapped:** 2026-09-24
**Repo read:** `/Volumes/ProjectsSSD/aihedgefund/repo` @ `feature/phase-07-regression-track-vertical-slice`
**Files analysed:** 13 (7 new source, 1 new script, 3+ new test modules, 1 new registry body, 2 config)
**Analogs found:** 12 / 13 (one genuine no-precedent: the Trainer `Protocol`)

Every line number below was read at this branch's HEAD. Cite them in plan
actions; re-grep before trusting one after another plan lands.

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match |
|---|---|---|---|---|
| `mvp/models/__init__.py` | package marker | — | `mvp/harness/__init__.py`, `mvp/sim/__init__.py` (both empty) | exact |
| `mvp/models/protocol.py` | interface declaration | — | **no analog** — zero `Protocol`/ABC in the repo; nearest is function-injection (`compute_code_hash(git_runner=...)`, `backfill/client.py:43-44`) | none — new convention |
| `mvp/models/regression.py` | service (fit) | batch / transform | `mvp/features/normalize.py:184-254` (`fit_normalization`/`fit_training_segment`) — the only existing "fit on train rows only, return parameters" module | role-match |
| `mvp/models/frozen.py` | model + registry-writer | CRUD (content-addressed JSON) | `mvp/harness/errata.py:296-377` (`errata_manifest_path`/`read_errata_manifest`) for the flat self-hashed JSON registry; `mvp/harness/negative_log.py:160-168` (`config_fingerprint`) for the id | exact (shape), see §C tension |
| `mvp/models/predictions.py` | model + store (write/load) | file-I/O + CRUD | **`mvp/features/normalize.py:307-414` + `:477-558`** — the ONLY manifest-addressed Parquet artifact on a non-date tier issued through `data.store.issue_manifest` | exact |
| `mvp/models/metrics.py` | utility | transform (pure numpy in → scalars out) | `mvp/sim/ticks.py` (dependency-light, constants + two pure functions, raises rather than returning a silent sentinel) | role-match |
| `mvp/models/<conversion>.py` (pred-return → pred-price) | utility | transform | `mvp/sim/ticks.py:105-160` (`price_to_ticks`) — same "one named deterministic rule, asserted not assumed" register | role-match, see §E |
| `mvp/scripts/<slice_runner>.py` | controller / CLI | batch orchestration | `mvp/scripts/holdout_declare_dry_run_real_lake.py` (real-lake posture, snapshot proof); `mvp/scripts/verify_live_connection.py:37-53` (argparse shape) | exact, with one inversion (§H) |
| `mvp/tests/models/conftest.py` | test fixture | — | `mvp/tests/harness/conftest.py` (copy verbatim) | exact |
| `mvp/tests/models/test_determinism.py` | test | — | `mvp/tests/sim/test_determinism.py` (whole file) | exact |
| `mvp/tests/models/test_*.py` (rest) | test | — | `mvp/tests/harness/test_segments.py`, `mvp/tests/features/test_normalize.py` | exact |
| `mvp/data/lake_registry/segments/<new-id>.json` | registry data | CRUD | the committed `97964cb27f62…json` body — copy every field but the geometry | exact |
| `mvp/data/store.py` (**MODIFIED**) | model (extend in place) | — | its own `FEATURES_NORM_TIER` declaration at `:98-101` | exact, see §A |
| `mvp/pyproject.toml` / `mvp/uv.lock` | config | — | themselves | exact |
| `mvp/spec.md` (**MODIFIED**) | doc | — | its own `## Simulator` section (`:197-364`) | exact |

---

## Pattern Assignments

### §A. `mvp/data/store.py` — a MODIFIED file, not just an import target

The planner must budget for editing `data/store.py`. `FEATURES_NORM_TIER`'s
own docstring made a promise for exactly one sibling:

```python
# mvp/data/store.py:92-111
#: The decision-row matrix tier (04-CONTEXT.md D-04-07), read by
#: `features.tier.load_features` and by nothing else.
FEATURES_TIER = "features"

#: The train-only normalization-parameter tier (D-04-06). Named here
#: alongside its sibling so the plan that uses it never has to edit this
#: file; it is deliberately NOT in `BY_DATE_INDEXED_TIERS` -- a
#: normalization artifact belongs to a fold, not to a date.
FEATURES_NORM_TIER = "features_norm"

#: Tiers whose manifests get a by-date `(dataset, symbol, stream, date)`
#: pointer. Everything else is addressable by `manifest_id` only.
BY_DATE_INDEXED_TIERS: frozenset[str] = frozenset({CURATED_TIER, FEATURES_TIER})
```

Add `PREDICTIONS_TIER = "predictions"` beside it, with a docstring in the same
register. **It must NOT enter `BY_DATE_INDEXED_TIERS`** — a prediction table
belongs to a `(segment_manifest, segment, predictor)` triple, not to a date,
and the allowlist's own comment says adding a member "is a deliberate,
reviewed act".

**The trap the planner must design around.** `issue_manifest` computes the
by-date set BEFORE consulting the tier:

```python
# mvp/data/store.py:427-431 (inside issue_manifest)
covered_dates = (
    dates if dates is not None else sorted({p["date"] for p in partitions})
)
if tier not in BY_DATE_INDEXED_TIERS:
    covered_dates = []
```

A predictions partition entry carries no `date` key, so the default branch
raises `KeyError`, not a clean error. `normalize.py:401-413` already names
this and passes `dates=[]` **explicitly**; the predictions writer must do the
same.

Two more runtime facts from `_enforce_tier_containment` (`:441-490`) that the
slice script must satisfy before the first read: `lake_root/predictions/` must
be a **real directory, not a symlink**, and every partition file must have
`st_nlink == 1`.

---

### §B. `mvp/models/predictions.py` — copy `features/normalize.py`, not `store.py` or `segments.py`

The question "which of `store.py` / `segments.py` / `errata.py` is the template
for a new `predictions` dataset?" has a fourth answer. What is common to those
three is only the content-addressing primitive:

```python
# mvp/data/store.py:141-151
def canonicalize_manifest(manifest: dict) -> bytes:
    body = {k: v for k, v in manifest.items() if k != "manifest_id"}
    return json.dumps(body, sort_keys=True).encode()

def compute_manifest_id(manifest: dict) -> str:
    return hashlib.sha256(canonicalize_manifest(manifest)).hexdigest()
```

They differ in the thing that matters here: **`segments.py` and `errata.py`
name no bytes on disk.** Their whole reason for existing as sibling registries
is `issue_manifest`'s refusal at `:337-341` ("a manifest that names nothing
verifies nothing"). A prediction table is ~270 MB of Parquet — it names bytes,
so it goes through `issue_manifest` like a normal tier.

`features/normalize.py` is the only existing module that does exactly that on
a **non-date-indexed** tier. Copy it structurally, function for function:

| normalize.py | predictions.py |
|---|---|
| `NORMALIZATION_ARTIFACT_SCHEMA` (`:96-111`) | `PREDICTION_TABLE_SCHEMA = {"etime": pl.Int64, "decision_seq": pl.Int64, "pred": pl.Float64}` (D-07-16) |
| `FEATURES_NORM_SCHEMA_VERSION = 1` (`:90`) | `PREDICTIONS_SCHEMA_VERSION = 1` |
| `normalization_dataset(symbol)` (`:149-154`) | `predictions_dataset(symbol)` → `"<SYMBOL>.predictions"` |
| `normalization_artifact_path(...)` (`:307-323`) | path keyed by D-07-14's triple, see below |
| `write_normalization_artifact(...)` (`:325-414`) | `write_prediction_table(...)` |
| `load_normalization(...)` (`:477-558`) | `load_prediction_table(...)` |
| `NormalizationArtifact` frozen dataclass (`:133-147`) | `PredictionTable` frozen dataclass |

**The path function** — mirror the "directory name says what this artifact
belongs to" reasoning at `:310-316`:

```python
# mvp/features/normalize.py:307-323 — the shape to copy
def normalization_artifact_path(lake_root: Path, symbol: str, train_end: str) -> Path:
    """`lake_root/features_norm/symbol=<symbol>/train_end=<date>/part-<ns>.parquet`.

    `train_end=` rather than `date=`, and no `stream=` level: this tier is
    addressed by manifest id, and the directory name says what the segment
    ENDED at rather than what day it "is" -- a normalization artifact
    belongs to a fold, not to a date (`BY_DATE_INDEXED_TIERS` deliberately
    excludes it).
    """
    return (
        Path(lake_root) / FEATURES_NORM_TIER / f"symbol={symbol}"
        / f"train_end={train_end}" / f"part-{time.time_ns()}.parquet"
    )
```

For predictions the directory key is D-07-14's triple:
`predictions/symbol=<SYMBOL>/segment_manifest=<id>/segment=<name>/predictor=<predictor_id>/part-<ns>.parquet`.
Keep the `part-<ns>` leaf — it is what makes the write-once glob guard work.

**The write-once guard** — copy the comment too; it records a real bug:

```python
# mvp/features/normalize.py:373-388
# THE PARENT, NOT THE FILE (04-REVIEW.md WR-05). This used to test
# `final_path.exists()` on a path whose name embeds `time.time_ns()`,
# constructed on the line above -- a guard that could never fire, so
# two artifacts for one `(symbol, train_end)` landed side by side each
# with its own manifest.
existing = sorted(final_path.parent.glob("part-*.parquet"))
if existing:
    raise FileExistsError(
        f"normalization artifact directory {final_path.parent} already "
        f"holds {existing[0].name} -- partitions are write-once. ..."
    )
write_parquet_atomic(body, final_path, compression="zstd")
```

**The partition entry** — seven fields, exactly (`size_bytes`/`mtime_ns` feed
`check_no_manifest_rewrite`'s fast leg; omitting them breaks a hook):

```python
# mvp/features/normalize.py:390-413
on_disk = final_path.read_bytes()
stat = final_path.stat()
partition_entry = {
    "path": str(final_path.relative_to(lake_root)),
    "sha256": hashlib.sha256(on_disk).hexdigest(),
    "rows": body.height,
    "size_bytes": stat.st_size,
    "mtime_ns": stat.st_mtime_ns,
    "etime_min": int(train_etime_range[0]),
    "etime_max": int(train_etime_range[1]),
}
return issue_manifest(
    dataset=..., symbol=symbol, stream=..., tier=...,
    schema_version=..., inputs=..., partitions=[partition_entry],
    code_hash=code_hash, registry_root=registry_root,
    dates=[],   # <-- REQUIRED, see §A
)
```

**The loader's two-gate reasoning transfers verbatim, and the planner must
copy the reasoning, not just the code** (`:477-558`):

- **Skip `_enforce_dq_pause`.** The gate asks for the DQ verdict of the DATE a
  partition covers; this tier's partitions carry no `date` and no DQ check
  emits a row for them, so routing through the gate reads "no rows" as
  `missing` and pauses every run forever. `normalize.py` has a named test
  pinning both halves (`test_load_normalization_does_not_enforce_a_dq_pause`)
  — write the twin.
- **Keep `assert_not_quarantined`.** A prediction column is a summary of the
  rows it was scored on. Derive the dates the way `_train_dates` (`:415-475`)
  does — from the upstream manifests, not from the artifact body — so the gate
  works on a table already on the lake without reissuing it.

**Reads the bytes twice, on purpose.** `resolve_manifest` hashes each partition
(`:531-537`) and then `read_verified_partitions` (`:1044-1067`) re-reads and
re-hashes the same buffer it parses. For a 270 MB table that is ~540 MB of
reads per load. Its docstring explains why the window matters; do not
"optimise" it. Budget the time in the slice runner instead.

---

### §C. `mvp/models/frozen.py` — the coefficient JSON

**`predictor_id` is `compute_manifest_id`, never a second hash.** The
precedent is explicit:

```python
# mvp/harness/negative_log.py:160-168
def config_fingerprint(config: dict) -> str:
    """The content-addressed id of `config` -- `data.store.
    compute_manifest_id` EXACTLY, the same function `harness.segments`
    uses for a segment manifest's own `manifest_id`. Never a second
    canonicalizer: two structurally-identical configs get the same
    fingerprint regardless of key order, exactly as two structurally-
    identical manifests get the same id."""
    return compute_manifest_id(config)
```

So `predictor_id = compute_manifest_id({estimator class name, sorted
hyperparameters, seed, code_hash, normalization manifest id})` — D-07-14's
five fields, and nothing else, as a plain dict.

**The self-hashed-flat-JSON registry shape** is `harness/errata.py:296-377`.
Copy the path function and the three-check read:

```python
# mvp/harness/errata.py:296-302
def errata_manifest_path(registry_root: Path, errata_id: str) -> Path:
    """Single source of truth for an errata manifest JSON's on-disk path --
    `registry_root/errata/<errata_id>.json`, flat, mirroring
    `harness.segments.segment_manifest_path`'s own shape."""
    return Path(registry_root) / "errata" / f"{errata_id}.json"
```

`read_errata_manifest` (`:304-377`) is the read template — three refusals, all
raising one dedicated error class:

1. **named-but-missing FAILS CLOSED** (never degrades to a default),
2. self-hash re-verified against **both** the id the caller passed **and** the
   body's own `manifest_id` field (a hand-edited body kept "helpfully in sync"
   is caught exactly as a stale one is),
3. cross-field agreement (`symbol`/`version`) with whatever named it.

And the dedicated exception class, with its stated reason:

```python
# mvp/harness/errata.py:58-68
class ErrataManifestError(ValueError):
    """...
    A dedicated class (not a bare `ValueError`, unlike this module's other
    raises) so `harness.accessor.materialize`'s fail-closed contract can be
    asserted against precisely -- a bare `ValueError` would also match
    `_find_entry`'s or `read_segment_manifest`'s own unrelated raises,
    silently passing a test for the wrong reason."""
```

Write `FrozenPredictorError(ValueError)` in that register. `ZeroVarianceError`
(`normalize.py:113-121`) is the other example of the house style: **name the
offender in the message, raise at the fit rather than let `inf` reach a loss
curve.**

**The re-evaluation test D-07-17 asks for.** `features/normalize.py:287-305`
is the frozen-transform precedent, and its docstring states the contract the
models test must assert:

```python
# mvp/features/normalize.py:287-296
def apply_normalization(values, params):
    """The FROZEN transform: `(values - mean) / std` with `(mean, std)` from
    a stored artifact and from nowhere else.

    Deliberately has no path that looks at `values` to decide `mean` or
    `std`. That is the whole contract ..."""
```

The sklearn-free re-evaluation (`intercept + X @ coef`, pure numpy, in a test
module that imports no `sklearn`) is the same idea as `sim/reference.py`'s
pure-Python twin — read that module's docstring (`:1-21`) for the register:
*"shares no arithmetic, no buffer and no state representation with the thing
it checks … Never imports anything from `sim.kernel`."*

**TENSION THE PLANNER MUST RESOLVE, NOT INHERIT.** Where does the coefficient
JSON live?

- **Under `lake_registry/predictors/`** — matches `segments/`/`errata/`, is
  git-committed, diffable, reviewable (D-07-17's stated motives). **Cost:**
  `check_manifest_append_only`'s `REGISTRY_DIR_NAMES` (`:150`,
  `frozenset({"manifests", "segments", "errata"})`) and
  `check_manifest_id_integrity`'s glob root both need extending, plus that
  checker's per-directory vacuity rule (`:583-591`). Phase 5 paid exactly this
  cost to add `segments/`/`errata/`; the work is known but non-trivial.
  Second wrinkle: the registry body's own self-hash equals `predictor_id`
  **only if the body is exactly D-07-14's five fields** — the moment the body
  also carries `coef`/`intercept`/`feature_names`, the two ids diverge and the
  file needs both (`predictor_id` as a field, `manifest_id` as the self-hash),
  the same double-id shape `read_errata_manifest` already checks.
- **As an MLflow artifact / a `predictions`-manifest `inputs[]` entry** — zero
  guardrail cost. D-07-17 bans MLflow artifacts for the *tables*; it says
  nothing about the coefficients. Surface this; don't assume it.

---

### §D. `mvp/models/protocol.py` — no precedent exists. State it plainly.

Grepped at HEAD across all of `mvp/` excluding `.venv`:

| Searched | Hits outside `.venv` |
|---|---|
| `\bProtocol\b` | **0** |
| `abc.` / `ABCMeta` / `abstractmethod` / `from abc` | **0** |
| `Callable[` | **2**, both in `mvp/data/backfill/client.py:43-44` |

This repo has **never** declared an interface. Pluggable behaviour is done
three other ways, and the planner should pick deliberately:

```python
# mvp/data/backfill/client.py:43-44 — named callable aliases
Fetcher = Callable[[str], bytes]
StreamOpener = Callable[[str], object]
```

```python
# mvp/tracking/mlflow_utils.py:106 — dependency injection by default argument
def compute_code_hash(dirty_suffix: str = "-dirty", git_runner=subprocess.run) -> str:
    """... `git_runner` is injectable so tests can supply a fake git command
    runner rather than depending on the real repo's dirty/clean state ..."""
```

```python
# mvp/sim/outputs.py:43 — NamedTuple for a result bundle
class SimResult(NamedTuple):
    trade_log: dict[str, np.ndarray]
    fill_count: int
    equity_scaled: np.ndarray
    counters: dict[str, int]
```

Plus `@dataclass(frozen=True)` for records (`normalize.py:123`, `:133`;
`spec/catalogue.py:56`, `:69`; `data/dq/checks.py` ×14).

**Recommendation to the planner (Claude's-discretion area per CONTEXT.md):**
declare `typing.Protocol` + `@runtime_checkable` as a NEW project convention,
with an `isinstance(estimator, Trainer)` test per estimator — otherwise the
protocol is a docstring the type checker never reads (`mypy` is listed in
CLAUDE.md's stack but is not among the 19 hooks, so nothing checks it in CI).
The `Trainer`/`FrozenPredictor` split should return a `NamedTuple`/frozen
dataclass result bundle in `SimResult`'s shape, and must not mention
`sklearn` anywhere in its signature — D-07-08's four estimators and Phase 8's
LightGBM/transformer have to satisfy the same two methods without a protocol
edit.

---

### §E. The unit gap nobody has written down: a return is not a price

This is the one thing in Phase 7 with no existing code and no CONTEXT decision.

`run_sim_checked`'s `pred` is a **raw USD price**:

```python
# mvp/sim/kernel.py:44-51 (module docstring, the quantisation rule)
    s = round(pred * PRICE_SCALE)                      # int64, s >= 0
    pred_ticks_floor = s // TICK_SIZE_SCALED            # floor(pred), long side
    pred_ticks_ceil  = -((-s) // TICK_SIZE_SCALED)      # ceil(pred), short side
    long_trigger  = pred_ticks_floor > ask_ticks + x_ticks
    short_trigger = pred_ticks_ceil  < bid_ticks - x_ticks
```

```python
# mvp/tests/sim/test_determinism.py:_price_at_ticks
"""A raw price (the scale `pred` arrives in) -- NOT `sim.ticks.price_to_ticks`'s
inverse (that is for bid/ask, never for a model prediction ...)"""
```

The model predicts `ret_10s_mid`, which `mvp/spec/labels.toml:22` defines as a
**simple** return:

```toml
[ret_10s_mid]
computation = "(mid_{t+10s} - mid_t) / mid_t"
```

So the slice needs `pred_price = mid_t * (1.0 + pred_ret)`, float64, and that
conversion deserves its own named function with its own test — the register of
`sim/ticks.py:price_to_ticks`, which asserts its round-trip rather than
assuming it.

**This is where D-07-09's refusal test gets subtle.** `mid` is a bookkeeping
column that must never enter the design matrix — *and it is required for the
price reconstruction*. The refusal must be scoped to the estimator's input
columns, not to "the module never touches `mid`". Write it as two tests:

1. passing a frame whose feature list includes `mid` / `bid_price` /
   `ask_price` **raises**;
2. the conversion function reads `mid` and the fitted coefficient vector has
   exactly three entries, in `("imb_top", "ofi", "trade_flow")` order;
3. for D-07-08's fourth estimator, the `PolynomialFeatures(degree=2,
   include_bias=False)` design matrix has **exactly 9 columns** (3 linear + 3
   squared + 3 cross) — not 14, not 20. A count assertion is what catches
   `mid`/`bid_price`/`ask_price` entering through an interaction term, which
   neither test 1 nor test 2 can see.

**`mid` never reaches the simulator.** `run_sim_checked` takes `bid_ticks`,
`ask_ticks` and `pred` only; its threshold offset is
`x_ticks = (b + a) * x_bps // 20_000` (`sim/kernel.py:356`), derived from the
two book sides, so the kernel needs no mid at all. The `mid` catalogue feature
is read by the conversion function and by nothing else.

Do not feed `mid` through `sim.ticks.price_to_ticks` either — at a 1-tick
spread the mid is a half tick 98.8% of the time (`spec/features.toml:24`) and
`price_to_ticks` refuses an exact half-tick round-trip by design
(`sim/ticks.py:144-156`). `bid_price`/`ask_price` are on-grid and are the only
two columns that go through it.

---

### §F. `mvp/scripts/<slice_runner>.py`

**Analog: `mvp/scripts/holdout_declare_dry_run_real_lake.py` (113 lines, read
it whole).** Its shape, point by point:

- **Module docstring carries the operating instructions**, including the
  literal invocation and the never-collected-by-pytest warning:
  ```
  NEVER COLLECTED BY PYTEST -- run manually from `mvp/`:

      ./.venv/bin/python3 -m scripts.holdout_declare_dry_run_real_lake [DATE]
  ```
  (`scripts/` has an empty `__init__.py` and is outside `testpaths = ["tests"]`,
  so nothing collects it; the warning is for the human.)
- **Positional argv, not argparse**, when there is one optional argument:
  `candidate_date = argv[0] if argv else DEFAULT_CANDIDATE_DATE`. The other
  script uses argparse when there are seven
  (`verify_live_connection.py:37-53`) — pick by count, and copy that file's
  *comment* convention of explaining any deviation from a "required" argument
  inline.
- **`main(argv) -> int` + `raise SystemExit(main(sys.argv[1:]))`** at the
  bottom. Both scripts. Non-zero on any failure, never a partial success.
- **Report with a `FAIL:` / `OK:` prefixed line naming the numbers**, e.g.
  `OK: dry_run against the real lake for {date} touched nothing ({n} lake
  files, {m} registry files unchanged).`
- **The before/after snapshot is the proof, not the prose**: `_snapshot(root)`
  walks a root recording `(size, mtime_ns)` per file, with an `os.walk`
  `onerror` hook that silently absorbs the quarantine tier's `PermissionError`
  — *and the script never names the quarantined directory anywhere*, which is
  also how it stays green under `check_lockbox_containment`.

**The one inversion.** The dry-run script deliberately does NOT call
`data.lake_paths.lake_root()`, because that function `mkdir`s and
write-probes. The Phase-7 slice runner **does write** (a `features_norm`
partition, a prediction table), so calling `lake_root()` is correct there —
state the difference explicitly in its docstring so a reader does not
"fix" it to match its analog.

**Two non-negotiables for this script:**

```python
# Top of file, BEFORE any import that can reach numba (conftest.py:20-23 precedent)
os.environ.setdefault("NUMBA_CACHE_DIR", "/tmp/nbc")   # never inside the repo
```
STATE.md records this as a Phase-4 blocker: `tests/conftest.py`'s pin only
applies under pytest, and the stray `*.nbc`/`*.nbi` land in a gitignored
`__pycache__` where only a repo-walk assertion can see them.

And D-07-05's cache: materialize **once** per segment, write the returned
frame to Parquet under a scratch dir **outside the repo and outside the
lake**, and have every later step read the cache. Reuse the snapshot helper
to prove the run created exactly the artifacts it claims and nothing else.

---

### §G. The new segment manifest body

**Analog:** the committed `mvp/data/lake_registry/segments/97964cb2…json`.
Copy every field but the geometry:

```
admission                    -> same policy dict, MINUS "counts" (derived at issuance)
budget_allowance             -> 3            (D-07-03, replacing 5)
errata_id                    -> "22190ad925929001855c4cec56c7ddbd00043d4717ec02ac96fc9b71e3e31b58"
layout                       -> "compressed_3seg"
symbol / version             -> "BTCUSDT" / 1
fold_config_reason           -> D-07-02's exact sentence (held-out declaration is Phase 8's SC4)
upstream_feature_manifest_ids-> the seven v2 feature manifests, not the three v1 ones
held_out                     -> zero-width sentinel: start_ns == end_ns == covered_end_ns
code_hash                    -> MUST NOT carry "-dirty" (D-07-07; the committed one does)
```

`issue_segment_manifest`'s signature (`harness/segments.py:571-588`) is where
the required keywords live — `lake_root`, `tracking_root` and
`fold_config_reason` are all required with no default, each for a stated
reason (`:609-648`). Three facts the plan needs:

- It **derives** `admission["counts"]`, `effective_intervals`,
  `purged_row_count`, `embargoed_row_count`, `purge_ns`, `embargo_ns` and
  `oof_training_row_counts` itself, from real partitions. Anything the caller
  supplies for those is discarded.
- It reads upstream rows through `features.tier.load_features`, **not**
  `harness.accessor.materialize` — issuance is not a counted look.
- It still needs a real `tracking_root` for the unconditional
  exhaustion-overlap self-discovery (`_refuse_overlap_with_exhausted_segments`,
  `:297-332`). Read-only against MLflow; permitted.

**Ordering for D-07-07's clean `code_hash`:** commit the issuing code first;
run the issuer from a clean HEAD; commit the manifest body in a second commit.
Use the dry-run script's snapshot helper to prove exactly one new file landed
under `segments/`.

---

## Shared Patterns

### Module docstring style — the highest-signal convention in this repo

Every module opens with a docstring that states **what the module is for, then
what it deliberately does NOT do, then why**, with an inline citation to the
decision or review finding that settled it (`D-05-11`, `04-REVIEW.md WR-05`,
`06-RESEARCH.md Q8`). Read `mvp/features/normalize.py:1-49` as the canonical
example: five ALL-CAPS sub-headings, each answering one objection a reviewer
would raise. `mvp/harness/errata.py:1-26` is the short form. `mvp/sim/ticks.py:1-22`
is the "here is what this module is NOT, so don't add it by reflex" form.

Every public constant gets a `#:` Sphinx-style comment explaining **why that
value** (`sim/ticks.py:44-50`, `store.py:88-113`). Every non-obvious `if` gets
an inline comment naming the review finding that put it there.

### Error convention

- Dedicated `ValueError` subclass **per protocol**, not per call site:
  `DQPauseError`, `ManifestTierError`, `ManifestHashMismatch`,
  `ErrataManifestError`, `ZeroVarianceError`, `BudgetExhaustedError`,
  `NegativeLogError`, `ZeroLotError`, `NotionalOverflowError`,
  `MissingTagError`, `SimStatusError`. The class docstring states *why a bare
  `ValueError` would let a test pass for the wrong reason*.
- Messages **name the offender and the fix**: `f"{path}: errata manifest hash
  mismatch -- expected {errata_id}, body's own manifest_id field
  {body_manifest_id!r}, recomputed {recomputed}"`.
- **Refuse, never return a neutral value.** `ZeroLotError` instead of `0`;
  `ZeroVarianceError` instead of `inf`; fail-closed instead of "mask nothing".
- **Never raise from inside an `@njit` function** — negative status code plus
  the offending row index, converted to an exception in the Python wrapper
  (`sim/kernel.py:468-482`). Applies if any Phase-7 code touches a kernel.

### Invariant assertion

Whole-array numpy checks with a first-offender lookup, never a Python loop:

```python
# mvp/sim/ticks.py:133-142
representable = np.abs(reconstructed - price) < _REPRESENTABILITY_EPSILON
if not np.all(representable):
    bad = int(np.flatnonzero(~representable)[0])
    raise ValueError(f"price_to_ticks: price[{bad}]={price[bad]!r} is not ...")
```

Schema equality as a single dict comparison, never per-column:

```python
# mvp/features/normalize.py:527-539
if body.schema != dict(NORMALIZATION_ARTIFACT_SCHEMA):
    raise ValueError(f"load_normalization: artifact schema {body.schema} does not match ...")
versions = set(body["schema_version"].to_list())
if versions != {FEATURES_NORM_SCHEMA_VERSION}:
    raise ValueError(...)
```

And the standing polars→numba rule from STATE.md: `.to_numpy()` on a Float64
column **with nulls** returns a NaN-filled copy and does not raise. Assert
`null_count() == 0` per column at every boundary — including the one into
`sklearn.fit`.

### MLflow

`tracking.mlflow_utils.start_tracked_run` is the only entry point
(`:216-263`); the eight `MANDATORY_TAG_KEYS` (`:76-88`) go in atomically at
run creation; anything additive uses `set_tags` afterwards, sharded via
`_shard_values` because **MLflow truncates an over-long tag value rather than
raising** (`log_data_provenance:266-322` — the WR-01 finding). Read a sharded
tag back only with `read_provenance_tag`.

`harness.negative_log.record_negative_result` (`:170-213`) is D-07-20's call;
note it does **not** fill `segment_manifest_id`/`model_class`/etc — the caller's
`run_tags` must already carry the other mandatory keys.

`mlflow.sklearn.autolog` does not exist here: `pyproject.toml` pins
`mlflow-skinny==3.13.*`, which ships no sklearn flavor (D-07-21).

### Cross-module helper policy

A five-line underscore-private I/O helper is **copied, not imported** —
`harness/segments.py:86-94` copies `data.store._atomic_write_json` and says so
in its docstring; `data.lockbox` and `harness.negative_log` do the same. A
private helper with a real *behavioural* contract is imported across the
boundary (`features/tier.py` calls `store._enforce_dq_pause`). Per-function
judgement, stated in the docstring either way.

---

## Test Conventions

**Naming.** Long, sentence-style, lowercase, `test_` + a full clause stating
the *behaviour and its reason*, routinely 60–90 characters. 913 tests at HEAD;
a representative dozen:

```
test_second_open_lockbox_raises_after_json_revert_because_mlflow_still_has_record
test_a_quote_inside_the_window_keeps_the_label_however_long_the_silence_before_it
test_untracked_segments_manifest_is_a_vacuous_pass_failure_naming_only_segments
test_issue_manifest_refuses_reuse_of_a_partition_path_under_another_spelling
test_read_segment_manifest_refuses_a_body_whose_manifest_id_field_disagrees
test_position_size_at_the_int64_overflow_bound_succeeds_one_tick_over_raises
test_record_look_refuses_a_non_canonical_root_without_creating_a_lock_dir
test_load_features_still_resolves_the_superseded_manifest_after_a_rebuild
test_unsliced_hash_is_sensitive_to_tail_garbage_sliced_hash_is_not
test_validation_data_cannot_change_the_parameters
test_two_same_process_runs_hash_identical
test_symmetric_quantisation_anti_vacuity_full_tick_beyond_triggers_both_sides
```

The pattern to reproduce: **name the refusal and the reason it is a refusal**
(`refuses_X_because_Y`, `X_succeeds_one_tick_over_raises`,
`X_is_sensitive_to_Y_Z_is_not`). Never `test_predictions` or
`test_write_prediction_table_works`.

**Anti-vacuity is a named, separate assertion, not an implication.** Every
invariance test carries a counterpart proving the fixture could have failed:

```python
assert result_a.fill_count > 0, "vacuous: the fixture never trades"
assert 0 < k < n, "the fixture must have both real trades and a genuine unfilled tail"
```

For Phase 7 that means: a rank-IC test must assert the non-tied subset is
non-empty; an R²-beats-zero test must assert the baseline is not degenerate;
a determinism test must assert the fit actually produced non-zero coefficients.

**`mvp/tests/models/` must NOT have an `__init__.py`.** `mvp/models/` exists;
a same-named test package shadows it. Five instances so far, every one a bug
(`tests/spec`, `tests/tools`, `tests/tracking`, `tests/features`, and the
Phase-2 recurrence). `--import-mode=importlib` is already set repo-wide
(`pyproject.toml:27-38`) precisely so same-basename test files in different
directories do not need a package marker.

**Fixtures.** Per-directory `conftest.py`, duplicated rather than shared.
Copy `mvp/tests/harness/conftest.py` **verbatim** — its three `tmp_path`-derived
roots plus two autouse fixtures are exactly what a models test needs:

```python
@pytest.fixture(autouse=True)
def isolated_canonical_tracking_root():   # clears AIHF_MLFLOW_TRACKING_ROOT before, restores after
@pytest.fixture(autouse=True)
def _end_any_active_run():                # so a failing test cannot leave "Run already active"
@pytest.fixture
def lake_root / registry_root / tracking_root   # the last one INITIALISES the sqlite store
```

Cross-directory shared builders live in `mvp/tests/fixtures/` (a real package,
*with* `__init__.py`, because no `mvp/fixtures/` exists). For anything that
needs `harness.accessor.materialize` to work hermetically, the one fixture
that makes it possible is:

```python
# mvp/tests/fixtures/harness_span.py:30-53
def build_span_partition(lake_root, registry_root, *, symbol="BTCUSDT", date,
                         start_ns, step_ns, rows, code_hash="deadbeef") -> dict:
```

It writes a real `FEATURE_ROW_SCHEMA` partition through the real writer, issues
the manifest, **and writes an `"ok"` DQ report row** — STATE.md records that
last part as a discovery: `load_features` unconditionally requires one via
`store._enforce_dq_pause`, and without it an otherwise-healthy synthetic
fixture raises `DQPauseError`.

**Hypothesis** is used in 12 modules. House shape: `@st.composite` strategy
building a whole *sequence* (never independent scalars), `@settings(deadline=None,
max_examples=50, suppress_health_check=[HealthCheck.too_slow])`, and
`hypothesis.assume` to filter for the interesting case rather than passing
vacuously (`tests/sim/test_kernel.py:482-549`). `.hypothesis/` is gitignored.

**Subprocess determinism** — `mvp/tests/sim/test_determinism.py` is the whole
template, three tests:

1. `test_two_same_process_runs_hash_identical`
2. `test_subprocess_run_hashes_identical_to_the_parent_process`
3. `test_unsliced_hash_is_sensitive_to_tail_garbage_sliced_hash_is_not`
   (the deliberate-sentinel mutation that makes #1 and #2 mean something)

Four mechanics to copy exactly:

- **Hash raw bytes, never a repr**: `h.update(arr.dtype.str.encode())`,
  `h.update(np.array(arr.shape, dtype=np.int64).tobytes())`, `h.update(arr.tobytes())`.
- **The child script is extracted, never retyped**:
  `"\n\n".join(textwrap.dedent(inspect.getsource(fn)) for fn in (...))` — the
  child runs byte-identical logic by construction. Functions destined for the
  child must not reference module-level names the child never defines; the
  file's own `_hash_result` docstring explains why it re-hardcodes a tuple.
- **The child env is explicit**:
  `env={**os.environ, "PYTHONPATH": str(MVP_ROOT), "NUMBA_CACHE_DIR": str(tmp_path/"...")}`,
  `cwd=str(MVP_ROOT)`, `capture_output=True`, `timeout=60`, then assert
  `returncode == 0` naming stdout/stderr.
- **Shape before value**: `assert re.fullmatch(r"[0-9a-f]{64}", child_stdout)`
  *before* comparing to the parent hash, so a crashed child with empty stdout
  cannot coincidentally "match".

For Phase 7, add D-07-12's thread pinning to both sides: the child env gets
`OMP_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, `MKL_NUM_THREADS=1` (BLAS thread
count changes float64 reduction order); the parent uses `threadpoolctl`, which
arrives as a `scikit-learn` dependency.

**Mutation checks are a PLAN-prose obligation, not committed code.** The
procedure is stated identically in every Phase-6 plan (e.g.
`06-02-PLAN.md:51`):

> Every mutation check PRINTS the exact block changed and asserts the file
> hash changed BEFORE running the suite; every invariance test has an
> anti-vacuity counterpart.

and the SUMMARY transcribes both hashes and the exact failure message.
Two hard-won refinements the Phase-7 plans should inherit:

- **A mutation can survive because the system is layered, not because the
  test is weak** (Phase 4, four instances). If a mutation does not bite, say
  which *other* gate caught it first.
- **The assertion must be sensitive to the mutated path.** Phase 6: a
  tick-only P&L assertion is quantity-blind and did not bite a
  flip-resizing mutation; the qty-weighted cross-check did.

---

## Numeric & Unit Conventions

| Suffix | Meaning | Where the constant lives |
|---|---|---|
| `_ns` | int64 nanoseconds since Unix epoch, or a duration in ns | `data/time_ns.py`: `NS_PER_SECOND=1_000_000_000` (`:57`), `NS_PER_DAY` (`:58`), `TRADE_FLOW_WINDOW_NS` (`:68`), `LABEL_HORIZON_NS: dict[str,int]` (`:79`). `harness/purge_embargo.py`: `PURGE_HORIZON_NS`, `FOLD_EMBARGO_NS` |
| `_scaled` | fixed-point integer at `QTY_SCALE`/`PRICE_SCALE` = `100_000_000` (1e-8) | `data/time_ns.py:96` (`QTY_SCALE`); `sim/ticks.py:44` (`PRICE_SCALE = QTY_SCALE`, "reuse the project's one 1e-8 idiom rather than inventing a second scale"), `:52` `TICK_SIZE_SCALED`, `:57` `LOT_STEP_SCALED`, `:62` `MAX_NOTIONAL_SCALED`, `:83` `MAX_NOTIONAL_SCALED_INT64_BOUND` |
| `_ticks` | int64 count of `TICK_SIZE_SCALED` units (0.1 USDT) | `sim/ticks.py` |
| `_bps` | integer basis points, used only as a comparison offset | `sim/kernel.py` `x_bps`, `fee_bps` |
| no suffix | raw float64 USD price or a dimensionless ratio | `bid_price`, `ask_price`, `mid`, `pred`, all four `ret_*` labels |

Rules that bite Phase 7:

- **Money is integer-exact.** The simulator's P&L is int64 ticks end to end.
  The overflow bound is **$922.34** of notional (`INT64_MAX // QTY_SCALE`),
  only 9.22× the $100 default, guarded by `STATUS_NOTIONAL_OVERFLOW`.
- **`pred` is float64 and stays float64** (D-07-16). The kernel does its own
  symmetric `floor`(long)/`ceil`(short) quantisation; a float32 rounding error
  near the half-tick boundary flips a trigger — the exact hazard Phase 6 spent
  a plan fixing.
- **Never re-multiply seconds.** Import `LABEL_HORIZON_NS` /
  `TRADE_FLOW_WINDOW_NS`; do not write `10 * NS_PER_SECOND`. See the
  `ms-to-ns-site` hook below — it resolves *values*, not literals, and
  `row_admission.py` needed an allowlist entry for exactly this.
- **Column names are literal catalogue lookups.** `features/tier.py:89-101`
  spells every one as `get_feature("imb_top").name` / `get_label("ret_10s_mid").name`
  — `check_catalogue_completeness` rejects a dynamically-constructed name, and
  that is what stops the model's feature list drifting from the catalogue.

---

## The Guardrail Surface — all 19 hooks

Every hook is `repo: local`, `language: system`, `pass_filenames: false`, and
runs **from the repo root** with its own `--directory mvp`. The command string
in `.pre-commit-config.yaml` `entry:` is **byte-identical** to the
corresponding `.github/workflows/ci.yml` `run:`. That parity is itself tested:
`mvp/tests/tools/test_ci_pre_commit_parity.py::test_every_pre_commit_command_is_a_ci_step_verbatim`
extracts both lists by plain string match and fails on any command present in
one and not the other, with exactly one named exception
(`check_no_manifest_rewrite` without `--full`, which CI cannot run). **If a
Phase-7 plan adds or edits a hook, it must make the same edit, character for
character, in both files, and the exception dict is the only escape.**

| # | Hook id | Can a Phase-7 file trip it? How to stay green |
|---|---|---|
| 1 | `ruff-check` | Yes — unused imports, `TID251` bans `import pandas` (incl. transitively in your own code). Run `uv run --locked --directory mvp ruff check .` before every commit. |
| 2 | `ruff-format` | Yes — trivially. Format before committing; the hook is `--check` only and will not fix for you. |
| 3 | `lockfile-check` (`uv lock --check`) | **Yes, directly.** Adding `scikit-learn==1.9.*` to `pyproject.toml` without re-running `uv lock` fails here. Run `uv lock` then `uv sync --frozen`. |
| 4 | `pin-assertion` (`check_pin_versions`) | **Yes.** It enumerates `PINNED_PREFIXES = {numba: 0.65, numpy: 2.4, llvmlite: 0.47}` and `BANNED_PACKAGES = {pandas}`, reading **every** `[[package]]` entry (forked resolutions included). sklearn needs no new entry, but the new lock must still show `numpy` at `2.4.*` and **zero** `pandas` entries at any marker. Re-check after `uv lock`, not before. |
| 5 | `ms-to-ns-site` | **Yes, easily.** It resolves *values* through a constant-folding environment, so `10 * NS_PER_SECOND` anywhere in `models/` is a second ms→ns site even though the literal `1_000_000` never appears. STATE.md records `row_admission.py` needing an allowlist entry for exactly this. Import `LABEL_HORIZON_NS["ret_10s_mid"]`; never multiply. |
| 6 | `catalogue-completeness` | **Yes.** Any `get_feature(...)`/`get_label(...)` call must take a **string literal** that exists in `spec/{features,labels}.toml`. Copy `features/tier.py:89-101`'s shape exactly. A name built from an f-string or a loop variable fails. |
| 7 | `latest-ban` | Low. Walks every string `ast.Constant` outside module/function/class docstrings for a `latest` path segment. Do not name a manifest pointer, a scratch dir, or an MLflow tag `latest`; say "most recent" in prose only inside a docstring. |
| 8 | `lockbox-containment` | **Yes — easily, and surprisingly.** The bare string `lockbox` in *any* file outside the sanctioned list fails. A Phase-7 docstring explaining why `load_prediction_table` calls `assert_not_quarantined` must not spell that word; say "the quarantined tier". STATE.md: a sanction disables every rule for a whole file, so new tests needing the literal go *into* an already-sanctioned file rather than extending the list. |
| 9 | `single-feature-path` | Low. Only `features/api.py`, the two implementation modules and `mvp/tests/` may import `features.kernel`/`features.reference`. Phase 7 has no reason to; go through `harness.accessor.materialize`. |
| 10 | `numba-globals` | No, unless Phase 7 writes an `@njit` function. It only inspects `@njit`-decorated functions for module-level non-constant global reads. |
| 11 | `spec-diff` | **Yes.** Fails if `mvp/spec.md`'s rendered catalogue tables drift from `spec/{features,labels}.toml`, **or** if a feature's `definition` / a label's `computation` changed under an existing name vs `--base-ref`. Phase 7's spec section (regression track, prediction-table contract, the two gates) is **new prose**, not a catalogue edit — safe. Do not "tidy" an existing `definition`/`computation` string; that is a hard fail with a re-render instruction. |
| 12 | `check-no-manifest-rewrite` (pre-commit, fast) | **Yes, from the moment the predictions manifest is committed.** It stat-compares each committed manifest's `(size_bytes, mtime_ns)` against the real mounted lake, on **every subsequent commit**. Consequence the planner must accept: the ~270 MB prediction table and the new `features_norm` partition must stay in place on `/Volumes/ProjectsSSD` forever, byte-identical, or every future commit on this machine fails. Deleting a prediction table to reclaim space is not a free action. |
| 13 | `check-no-manifest-rewrite-full` (**pre-push**) | Same, with sha256 over every partition. On push this re-reads the whole lake including the new 270 MB table — expect the push to be slower. SKIPs (exit 0) where the lake is unmounted. |
| 14 | `check-no-manifest-rewrite-fixture` | No. Points the `--full` scan at the committed `tests/fixtures/lake`. Only trips if Phase 7 touches that fixture. |
| 15 | `check-manifest-id-integrity` | **Yes.** Recomputes each committed manifest's id from its own body and compares against both the `manifest_id` field and the filename. Needs no lake. Applies to the new segment-manifest body, and to a `predictors/` registry only if that directory is added to its glob root (§C). |
| 16 | `check-manifest-append-only` | **Yes.** Git-history anchored, `REGISTRY_DIR_NAMES = {"manifests", "segments", "errata"}` (`:150`). No committed manifest may be deleted or modified, ever; no two manifests may name one partition path with different sha256. Reissuing the Phase-5 segment manifest, or "fixing" its `-dirty` code_hash in place, is a hard fail — D-07-01's "NOT reused and NOT modified" is enforced here, not just documented. A shallow clone FAILS (never SKIPs). Adding a `predictors/` registry means extending this set **and** its per-directory vacuity rule (`:583-591`). |
| 17 | `check-harness-accessor-only` | **Yes — this is the Phase-7 hook most likely to bite.** Only `mvp/harness/` (whole directory) and **five individually named test files** may `import features.tier.load_features`. `models/`, `scripts/` and `tests/models/` are all scanned like anything else. Every fold read goes through `harness.accessor.materialize`. If a models test genuinely needs the tier directly to check what wraps it, that is a sixth sanctioned file and a deliberate edit to the checker — the same call Phases 5 and 6 each made once, each time with a written reason. |
| 18 | `leakage-suite` (`pytest tests/leakage -x -q`) | Only if Phase 7 changes a feature/label information set — it should not. Deliberately redundant with #19 so a leakage break has its own line in the log. |
| 19 | `pytest` (`pytest tests -x -q`) | **Yes, always.** The full 913+ test suite, `-x`, on every commit. There is no `--no-verify` in this project. Two consequences: a slow models test slows every commit, and Phase 7's plans land in bigger commits than they look like on paper (STATE.md, Phase 6: two tasks landed in one commit because the hook runs the whole suite and the intermediate state was red). |

---

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| `mvp/models/protocol.py` | interface | — | Zero `typing.Protocol`, zero ABC in the repo (§D). The planner is establishing a convention, not following one. Use RESEARCH.md / CLAUDE.md, and add an `isinstance` test per estimator so the declaration is load-bearing. |
| the return→price conversion (§E) | utility | transform | No code anywhere converts a label back into the scale `run_sim_checked` expects. Shape is borrowed from `sim/ticks.py:price_to_ticks`; the arithmetic is new. |
| `metrics.py`'s rank-IC-on-the-non-tied-subset | utility | transform | `scipy.stats.spearmanr` arrives transitively with sklearn and has never been imported here. The tie-fraction-as-a-first-class-number discipline (D-07-18) has no in-repo precedent; the nearest register is `spec/labels.toml:25`'s insistence on naming the row set of every measured statistic. |

---

## Open Tensions for the Planner

1. **Where the coefficient JSON lives** (§C) — a `lake_registry/predictors/`
   registry costs two guardrail edits; an MLflow artifact or a
   `predictions`-manifest `inputs[]` entry costs none. D-07-17's "not as MLflow
   artifacts" is written about the *tables*. Decide explicitly.
2. **`predictor_id` vs `manifest_id`** — if the registry body carries the
   coefficients, its self-hash is not `predictor_id`. Plan for both fields and
   the `read_errata_manifest`-style double check, or keep the body to exactly
   D-07-14's five fields and store coefficients elsewhere.
3. **The perfect-foresight ceiling number** (D-07-19) is settled and CONTEXT.md
   is right: **2,192 trades / 294,554 ticks / $29.46** on the v2 2026-09-13
   partition. `06-06-SUMMARY.md` and
   `.planning/phases/06-.../evidence/06-06-real-day-oracle.json` report
   2,212 / 293,844 — those are **pre-fix** figures, superseded by 06-07's
   symmetric floor/ceil quantisation fix (the evidence file says so in its own
   `SUPERSEDED_BY` key). Cite STATE.md's `stopped_at`, not the evidence JSON.
   And note the ceiling is a *per-segment* quantity — the new `val` segment's
   own must be re-measured before it bounds anything.
4. **The 270 MB table is a permanent commitment** (hook #12). Measure and
   record the real bytes as D-07-15 requires, and state in the SUMMARY that
   deleting it later breaks every subsequent commit until its manifest is
   dealt with.
5. **`spec.md` gains a section, and the `## Contents` list at `:38-56` must
   gain a matching anchor.** Mirror `## Simulator`'s structure (`:197-364`):
   a top-level heading with `###` subsections for the prediction-table
   contract, the two baseline gates, and the determinism guarantee.

---

## Metadata

**Analog search scope:** `mvp/{data,features,harness,sim,spec,tools,tracking,scripts,tests}/`,
`.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `.planning/STATE.md`,
`.planning/phases/0{5,6}-*/`. `.venv/` excluded throughout.
**Source files read in full or in targeted ranges:** 28.
**Analogs selected:** 5 primary (`features/normalize.py`, `harness/errata.py`,
`scripts/holdout_declare_dry_run_real_lake.py`, `tests/sim/test_determinism.py`,
`tests/harness/conftest.py`) plus `data/store.py` as a modified file.
**Pattern extraction date:** 2026-09-24.
**Read-only:** no source file was modified; this document is the only write.
