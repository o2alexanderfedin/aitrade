# Phase 5: Fold Harness & Overfitting Controls - Pattern Map

**Mapped:** 2026-09-20
**Files analyzed:** 21 (11 new source, 4 new registries/dirs, 4 modified guardrails, 2 config files)
**Analogs found:** 21 / 21 (every file has a working, three-times-reviewed precedent — CONTEXT.md and RESEARCH.md already name most analogs explicitly; this document pins exact line numbers and excerpts)

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `mvp/harness/segments.py` | model/registry-writer | CRUD (content-addressed write, no partitions) | `mvp/data/store.py` (`canonicalize_manifest`/`compute_manifest_id`, NOT `issue_manifest`) | role-match (sibling writer, deliberately not the same function) |
| `mvp/harness/purge_embargo.py` | utility | transform (duration derivation) | `mvp/spec/information_set.py` (`parse_embargo`) + `mvp/data/time_ns.py` (`LABEL_HORIZON_NS`, `TRADE_FLOW_WINDOW_NS`) | exact (import, don't reparse) |
| `mvp/harness/kfold.py` | utility | batch (block partitioning + invariant checks) | none in-house (net-new algorithm, CLAUDE.md explicitly mandates hand-rolling this) | no analog — see below |
| `mvp/harness/accessor.py` | service/gated-accessor | request-response (the one `load_features` wrapper) | `mvp/features/tier.py:load_features` (four-gate ordered accessor) | exact |
| `mvp/harness/budget.py` | service/durable-counter | event-driven (MLflow-first count-and-refuse) | `mvp/data/lockbox.py:_mlflow_has_consumed` + `_require_canonical_tracking_root` + `_require_initialised_mlflow_store` | exact |
| `mvp/harness/negative_log.py` | service/durable-log | event-driven (MLflow run as a record) | `mvp/data/lockbox.py`'s MLflow-first shape + `mvp/data/store.py:compute_manifest_id` (fingerprint) | role-match |
| `mvp/harness/holdout_declare.py` | controller/CLI orchestration | CRUD (dry-run report + orchestrated move) | `mvp/data/lockbox.py:open_lockbox` (orchestration shape: check → lock → stamp → act) | role-match |
| `mvp/harness/row_admission.py` | utility | transform (polars expression over an already-loaded frame) | inline in RESEARCH.md Q7 (verified expression); no prior file-level analog, shape borrowed from `features/tier.py`'s pure-polars style | partial (algorithm is new, but the polars idiom is the project's existing style) |
| `mvp/data/lockbox.py` (+ new public fn) | service (extended) | file-I/O (physical partition move) | its own existing `open_lockbox`/`issue_token`/`_atomic_write_json` pattern | exact (extend in place) |
| `mvp/data/holdout.py` (+ new writer fn) | model/registry (extended) | CRUD (first writer added to a reader-only module) | its own existing `quarantined_dates`/`_registry_problem` fail-closed reader pattern | exact (extend in place) |
| `mvp/data/lake_registry/segments/<id>.json` | registry data | CRUD | `mvp/data/lake_registry/manifests/<dataset>/<id>.json` shape (sibling, no `partitions` key) | role-match |
| `mvp/data/lake_registry/errata/<id>.json` | registry data | CRUD | same sibling-registry shape as `segments/` | role-match |
| `mvp/tools/check_manifest_append_only.py` (extended) | guardrail/CI | batch (git-history scan) | itself — generalize the single `MANIFESTS_DIR_NAME` scan to cover `segments/`/`errata/` (bigger than a one-line change — see below) | exact (extend in place) |
| `mvp/tools/check_manifest_id_integrity.py` (extended) | guardrail/CI | batch (JSON self-hash scan) | itself — generalize `_iter_manifest_files`'s single `"manifests"` glob root | exact (extend in place) |
| `mvp/tools/check_no_manifest_rewrite.py` | guardrail/CI | batch | **NOT extended** — left pointed at `manifests/` only (would fail every segment manifest by design) | n/a — explicitly out of scope |
| `mvp/tools/check_harness_accessor_only.py` | guardrail/CI (new) | batch (AST scan, N sanctioned callers) | `mvp/tools/check_single_feature_path.py` (narrower "one function, N callers" shape — closer fit than `check_lockbox_containment.py`'s "whole module" shape) — **but see the name-collision gap below before copying the watch-list verbatim** | exact shape, gap flagged |
| `mvp/tests/harness/` (new root, NO `__init__.py`) | test | — | `mvp/tests/tracking/`, `mvp/tests/lockbox/` (existing no-`__init__` test roots) | exact |
| `mvp/tests/harness/conftest.py` | test fixture | — | `mvp/tests/fixtures/feature_build.py` (`quote_frame`, real-UTC-midnight-anchored etimes) for span; `mvp/tests/tracking/test_mlflow_utils.py` (bare `tmp_path` MLflow pattern) | exact, with a fixture-choice caveat below |
| `mvp/tests/tools/test_check_harness_accessor_only.py` | test | — | `mvp/tests/tools/test_check_single_feature_path.py` | exact |
| `.pre-commit-config.yaml` (+1 hook, +ext to 2 existing) | config | — | itself — existing `entry:` line shape (no `files:` regex scoping exists on any manifest-guardrail hook — see below) | exact |
| `.github/workflows/ci.yml` (+1 step, +ext to 2 existing) | config | — | itself — existing `run:` line shape, byte-identical to pre-commit | exact |

## Pattern Assignments

### `mvp/harness/segments.py` (registry-writer, content-addressed)

**Analog:** `mvp/data/store.py` — reuse `canonicalize_manifest`/`compute_manifest_id`; do **NOT** call `issue_manifest`.

**Why not `issue_manifest`** (`mvp/data/store.py:304-345`, confirmed live):
```python
def issue_manifest(
    dataset: str, symbol: str, stream: str, tier: str, schema_version: int,
    inputs: list[dict], partitions: list[dict], code_hash: str,
    *, registry_root: Path, dates: list[str] | None = None,
) -> dict:
    ...
    if not partitions:
        raise ValueError(
            "issue_manifest: no partitions -- a manifest that names nothing "
            "verifies nothing (03-REVIEW-ITER2.md IN-15)"
        )
```
A segment manifest names no bytes of its own (D-05-09) — its body must **omit** the `partitions` key entirely (not `[]`), so `segments.py` writes a sibling function, not `issue_manifest`.

**Content-addressing primitives to reuse verbatim** (`mvp/data/store.py:140-151`):
```python
def canonicalize_manifest(manifest: dict) -> bytes:
    """Return the canonical byte encoding of `manifest`'s body, EXCLUDING
    the `manifest_id` key itself -- the id is computed FROM the rest, never
    included in what it hashes (avoids a self-referential hash)."""
    body = {k: v for k, v in manifest.items() if k != "manifest_id"}
    return json.dumps(body, sort_keys=True).encode()


def compute_manifest_id(manifest: dict) -> str:
    """Return the sha256 hex digest of `canonicalize_manifest(manifest)`."""
    return hashlib.sha256(canonicalize_manifest(manifest)).hexdigest()
```
These two are safe to `import` directly (public, no leading underscore).

**Atomic write helper — DUPLICATE it, do not import it.** `_atomic_write_json` is underscore-private in `mvp/data/store.py:297-301`:
```python
def _atomic_write_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(body, sort_keys=True, indent=2))
    tmp_path.replace(path)
```
The project's own stated norm is to duplicate a small, stable, private helper rather than import a leading-underscore name across a module boundary — `mvp/tools/check_manifest_id_integrity.py:47-51` states this explicitly for `BY_DATE_MARKER`, and `mvp/data/lockbox.py:115-119` already duplicates this exact function rather than importing `data.store._atomic_write_json`. `harness/segments.py` should do the same: copy the 5-line function in, don't import it. (Counter-example the planner should also know about: `features/tier.py:load_features` DOES call `store._enforce_dq_pause`/`store._log_provenance` across the module boundary — that boundary-crossing precedent exists too, for functions with real behavioral contracts rather than a five-line I/O helper. Which precedent applies is a per-function judgment call, not a blanket rule; for `_atomic_write_json` specifically, duplicate.)

**Path convention to mirror** (`mvp/data/store.py:153-155`, `manifest_path`):
```python
def manifest_path(registry_root: Path, dataset: str, manifest_id: str) -> Path:
    """Single source of truth for a manifest JSON's on-disk path."""
    return Path(registry_root) / "manifests" / dataset / f"{manifest_id}.json"
```
`segments.py`'s own `segment_manifest_path(registry_root, manifest_id)` should follow the same "single source of truth" shape, at `registry_root/segments/<id>.json` (flat — D-05-08 says one manifest per fold layout, no dataset subdirectory needed unless the planner wants dataset-scoping parity).

**Errata registry** (`mvp/data/lake_registry/errata/<id>.json`): same treatment — same `canonicalize_manifest`/`compute_manifest_id`/duplicated-`_atomic_write_json` triple, no `partitions` key.

---

### `mvp/harness/purge_embargo.py` (duration derivation)

**Analog:** `mvp/spec/information_set.py:parse_embargo` + `mvp/data/time_ns.py:LABEL_HORIZON_NS`/`TRADE_FLOW_WINDOW_NS` — import, never reparse.

**Anti-pattern this file must avoid** (confirmed in RESEARCH.md Q2, verified against `mvp/tests/leakage/test_embargo.py:64-72`): `test_the_embargo_bound_is_tight_enough_to_bite` asserts, for every catalogue label, `parse_embargo(entry.embargo) == LABEL_HORIZON_NS[name]` **exactly**. The purge horizon (`h_max`) is `max(LABEL_HORIZON_NS.values())` (600 s today) — import the dict, never re-derive from `spec/labels.toml` strings.

**The fold-boundary embargo constant** — a **new** declaration, not an import of the catalogue embargo:
```python
# harness/purge_embargo.py — the shape the planner must write
from data.time_ns import TRADE_FLOW_WINDOW_NS

FOLD_EMBARGO_NS = TRADE_FLOW_WINDOW_NS  # policy constant, not re-derived
```
paired with a pinning test asserting (a) `ofi`'s catalogue `information_set` string is still `"[prev_l1_update, t]"` and (b) `TRADE_FLOW_WINDOW_NS == FOLD_EMBARGO_NS` — see RESEARCH.md Q2 for the exact two-assertion shape.

---

### `mvp/harness/kfold.py` (purged + embargoed inner k-fold OOF)

**No existing analog** — CLAUDE.md's "Alternatives Considered" table explicitly mandates hand-rolling ~150 LOC here over skfolio/mlfinlab/timeseriescv (all rejected for stated reasons). This is the one genuinely new algorithm in the phase.

**Row-selection idiom to copy** (the project's established half-open-interval style, `data/store.py`/`features/tier.py`'s general polars style; exact shape given in RESEARCH.md "Code Examples"):
```python
segment_rows = df.filter(
    (pl.col("etime") >= start_ns) & (pl.col("etime") < end_ns)
)
```

**Invariant-test discipline to copy** (project-wide pattern, e.g. `mvp/tests/leakage/`): every invariant test needs an anti-vacuity counterpart — shrinking `FOLD_EMBARGO_NS`/purge horizon to 0 on a real-horizon fixture must make at least one overlap appear that the full-strength config correctly excludes (RESEARCH.md Q8, invariant 3). **This directly depends on the fixture-span warning below** — a fixture whose blocks are already far apart relative to `h_max` passes vacuously regardless of how carefully the invariant test is written.

---

### `mvp/harness/accessor.py` (the ONE `load_features` caller for validation rows)

**Analog:** `mvp/features/tier.py:load_features` (lines 473-536) — same four-gates-in-order shape, budget check becomes a fifth gate.

```python
def load_features(
    manifest_id: str, dataset: str, *, registry_root: Path, lake_root: Path
) -> pl.DataFrame:
    """... Four gates, in this order, and the ORDER IS THE POINT:
    1. resolve_manifest(expected_tier=FEATURES_TIER) -- integrity FIRST
    2. the holdout refusal, over refused_dates_for(manifest)
    3. store._enforce_dq_pause
    4. store._log_provenance, then the verified read.
    """
    manifest = store.resolve_manifest(
        manifest_id, dataset, registry_root=registry_root,
        lake_root=lake_root, expected_tier=FEATURES_TIER,
    )
    assert_not_quarantined(
        refused_dates_for(manifest), symbol=manifest["symbol"],
        registry_root=registry_root, context=(...),
    )
    acks = store._enforce_dq_pause(manifest, registry_root=registry_root, lake_root=lake_root)
    store._log_provenance(manifest, acks)
    frames = store.read_verified_partitions(manifest, lake_root=Path(lake_root))
```

`harness/accessor.py` copies this ordered-gates shape: (1) resolve the **segment** manifest, re-verify upstream feature manifests via `resolve_manifest`, (2) refuse `role == "held_out"` unconditionally (D-05-10, independent of `holdout.json`), (3) apply row-admission exclusion (D-05-21) + errata null-masking (D-05-20), (4) budget check-and-increment (D-05-11..15) — this is the "look," counted **before** returning. Integrity/refusal gates still come **first**, exactly like `load_features`'s own ordering rationale ("integrity FIRST: a manifest that fails its own hash must never reach a holdout... conversation").

Note this module is itself a caller of `features.tier.load_features` for each upstream feature manifest a segment references — it is the file `check_harness_accessor_only.py` must sanction (see that entry's name-collision warning below).

---

### `mvp/harness/budget.py` (MLflow-first durable counter)

**Analog:** `mvp/data/lockbox.py` — copy the exact three-function shape: `_require_canonical_tracking_root`, `_require_initialised_mlflow_store`, `_mlflow_has_consumed`.

**The exact shape to copy** (`mvp/data/lockbox.py:243-312`):
```python
def _mlflow_has_consumed(
    token_id: str, tracking_root: str, *, allowed_root: str | None = None
) -> bool:
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
```
`budget.py`'s counter swaps the filter for `f"tags.segment_manifest_id = '{mid}' and tags.segment_name = '{name}'"` (D-05-13's granularity — confirmed cheap: 0.0088 s measured against a 50-run store per RESEARCH.md Q3) and swaps `LockboxTokenError` for a budget-specific exception. **`segment_name` is an additive tag, not a `MANDATORY_TAG_KEYS` addition** — `mlflow_utils.MANDATORY_TAG_KEYS` (below) must not change. This helper trio (unlike `_atomic_write_json`) has real behavioral contracts (SQLite header/table checks, canonical-root pinning) — copy the logic, and decide file-by-file whether to duplicate or cross-import, same as the `segments.py` note above.

**Init-store guard to copy verbatim** (`mvp/data/lockbox.py:169-215`, `MLFLOW_STORE_REQUIRED_TABLES`, `_SQLITE_HEADER`, `_require_initialised_mlflow_store`) — same SQLite-header + table-presence check, same "never construct a client against an uninitialized store" fail-closed direction.

**Same exception-propagation rule**: "Any exception raised by `MlflowClient(...)`... PROPAGATES UNMODIFIED — `False` here means 'the query succeeded and found nothing', never 'the query could not be run'" (docstring, `mvp/data/lockbox.py:281-283`). `budget.py` must state and test the same contract.

---

### `mvp/harness/negative_log.py` (failed-config record, queryable)

**Analog:** same MLflow-first shape as `budget.py`, fingerprint via `data.store.compute_manifest_id` (the same canonicalizer, per D-05-22 — "not a second one").

```python
# fingerprint = compute_manifest_id(canonicalized config dict) -- same function
# quoted above under segments.py; do not reimplement.
```
Query shape mirrors `_mlflow_has_consumed`'s `search_runs(experiment_ids, filter_string=..., run_view_type=ViewType.ALL)` call, filtering on an `outcome` tag instead of `lockbox_token_id`.

---

### `mvp/harness/holdout_declare.py` (dry-run + orchestrated declaration)

**Analog:** `mvp/data/lockbox.py:open_lockbox` (lines 382-456) for the check→lock→stamp→act **orchestration shape** (not the lockbox-path join itself — that stays inside `data/lockbox.py`, see below).

**Reader analog for what it must refuse against** — `mvp/data/holdout.py`'s fail-closed reader, full file, especially:
```python
def quarantined_dates(*, registry_root: Path | None = None, symbol: str) -> QuarantinedDates:
    """An ABSENT registry returns an empty set with declared is False...
    Anything else that is not a well-formed registry for THIS symbol RAISES."""
    path = holdout_registry_path(registry_root)
    if not path.exists():
        ...
        return QuarantinedDates((), declared=False)
    ...
    problem = _registry_problem(body, symbol=symbol)
    if problem is not None:
        raise ValueError(f"{path}: {problem}")
    return QuarantinedDates(body["dates"], declared=True)
```
`holdout_declare.py` adds the **writer** half (D-05-17/18): a new function, either inside `data/holdout.py` (recommended — it already owns the schema) or in the harness module, that writes `{"version": 1, "symbol": ..., "dates": [...], "locked_at": <ns>, "reason": ...}` via the duplicated `_atomic_write_json` pattern (see the reasoning under `segments.py` above).

**The dry-run's "which manifests would stop resolving" logic already has a live analog** — `mvp/features/tier.py:load_features`'s own docstring (lines 508-512, verified):
```
AND THE PARTITION THAT STEP MUST MOVE IS NOT THE OBVIOUS ONE. Declaring
day X held out makes `features/date=X` (which may not exist -- the most
recent day never does) AND `features/date=X-1` unreadable here, because
X-1's label tail is X's price path. It is the day BEFORE the declared
date that carries the held-out bytes.
```
and `refused_dates_for`'s (lines 443-470) derivation, `{next_utc_date(date) for date in dates}` — the exact `D_lock`/`D_lock − 1` relationship D-05-18 restates. The dry-run's report of "exactly which partitions would move and which manifests would stop resolving" should walk this same `next_utc_date` derivation forward from the declared date(s), not reinvent it.

**The one operation `holdout_declare.py` must NOT perform itself**: joining a path under `lake/lockbox/`. Per `check_lockbox_containment.py` (verified `mvp/tools/check_lockbox_containment.py:249`, `SANCTIONED_FILES = frozenset({"data/lockbox.py", "tools/check_lockbox_containment.py"})`), only `data/lockbox.py` may do that. `holdout_declare.py` freely imports and calls the **public** sanctioned names:
```
open_lockbox, issue_token, LockboxTokenError, token_path
```
(confirmed sanctioned-usage line, `mvp/tools/check_lockbox_containment.py:142-143`: *"Importing and calling PUBLIC names (`open_lockbox`, `issue_token`, `LockboxTokenError`, `token_path`) is the sanctioned usage."*) — but the actual partition move is a **new public function added inside `data/lockbox.py`** (see next entry).

**`--dry-run` needs no write access**, confirmed by the barrier doc (`mvp/data/lockbox_POLICY.md`, quoted in RESEARCH.md Q6): the `chmod 0000`/`chmod 0755` dance around `lake/lockbox/` is manual and out-of-band; `holdout_declare.py` never calls `chmod` itself.

---

### `mvp/data/lockbox.py` — new public function (physical move)

**Analog:** its own `open_lockbox`'s ordering discipline (verify → lock → stamp-before-read/move → act) and `issue_token`'s write-once-refuse-overwrite posture (`mvp/data/lockbox.py:122-164`):
```python
def issue_token(...) -> dict:
    path = token_path(token_id, registry_root=registry_root)
    if path.exists():
        raise LockboxTokenError(f"lockbox token already exists: {token_id}")
    ...
    _atomic_write_json(path, body)
    return body
```
The new function (e.g. `quarantine_feature_partition(date, ...)`) is the **only** code permitted to construct `Path(lake_root) / "lockbox" / ...` (per `check_lockbox_containment.py`'s `SANCTIONED_FILES` set above).

**Dataset naming for the moved manifest — a strong extrapolation, not a confirmed fact.** The one committed fixture that issues a lockbox-tier manifest is a synthetic **trade** segment, not a features one:
```python
# mvp/tests/lockbox/test_token_one_look.py:98-108 (_build_segment)
manifest = issue_manifest(
    dataset="BTCUSDT.trade",   # SAME dataset name pattern as curated/features
    symbol="BTCUSDT",
    stream="trade",
    tier="lockbox",            # ONLY the tier changes
    schema_version=1,
    inputs=[],
    partitions=[part],
    code_hash="deadbeef",
    registry_root=registry_root,
)
```
This shows the convention "only `tier` changes, `dataset` stays `{symbol}.{stream}`" for a **trade** partition. It is a reasonable, natural extrapolation that a lockbox-tier **features** manifest would use `dataset="BTCUSDT.features"`, `tier="lockbox"` — but no fixture in the codebase issues that combination today, so this resolves RESEARCH.md's Open Question 2 only as a recommendation, not a verified fact. The planner should either confirm this reading explicitly (e.g., against `data.store.BY_DATE_INDEXED_TIERS`'s deliberate exclusion of the lockbox tier) or treat the dataset name as an open decision to record in the plan.

**The `chmod` barrier's implication, stated for the plan**: the new function must not lift or reapply `chmod 0000`/`0755` — that stays a human, out-of-band step (module docstring, `mvp/data/lockbox.py:1-51`, "This module never lifts that barrier itself").

---

### `mvp/data/holdout.py` — new writer function

**Analog:** its own existing fail-closed reader shape, and the fixture that already writes this exact file for tests:
```python
# mvp/tests/fixtures/feature_tier.py:139-157
def write_holdout_registry(
    registry_root: Path, dates: list[str], *, symbol: str = SYMBOL, body: object = None
) -> Path:
    from data.holdout import holdout_registry_path
    path = holdout_registry_path(registry_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if body is None:
        body = {
            "version": 1, "symbol": symbol, "dates": dates,
            "locked_at": 1_700_000_000_000_000_000, "reason": "test fixture",
        }
    path.write_text(body if isinstance(body, str) else json.dumps(body))
    return path
```
The real, non-test writer Phase 5 adds should mirror this shape but through the duplicated `_atomic_write_json` (tmp-then-rename), not a bare `write_text` — matching the durability posture every other registry writer in the codebase uses.

---

### `mvp/tools/check_manifest_append_only.py` (extend to cover `segments/`/`errata/`)

**Analog:** itself, but the change is bigger than "swap equality for set membership" — `MANIFESTS_DIR_NAME` has **three** use sites, not one, and they need two different kinds of fix.

**Site 1 — `_is_manifest_shaped` (`mvp/tools/check_manifest_append_only.py:333-343`), the cheap generalization:**
```python
def _is_manifest_shaped(repo_rel: str) -> bool:
    """Is `repo_rel` a manifest of ANY registry, wherever it lives (rule 2,
    WR-17)? A `.json` below a `manifests` directory, not inside a pointer
    directory below it."""
    if not repo_rel.endswith(".json"):
        return False
    dirs = repo_rel.split("/")[:-1]
    if MANIFESTS_DIR_NAME not in dirs:
        return False
    rest = repo_rel.split("/")[dirs.index(MANIFESTS_DIR_NAME) + 1 :]
    return not _is_pointer("/".join(rest))
```
This one generalizes trivially: `if not any(d in REGISTRY_DIR_NAMES for d in dirs): return False`, then locate whichever name matched. It is used by `_head_manifest_blobs` and `_worktree_manifest_blobs` (lines 291-330, the whole-repo, whole-history content-anchoring scan) — those two need **no other change**, since they already walk the entire repo tree and just ask `_is_manifest_shaped`/`_realm` per path.

**Site 2 — `check_append_only()`'s single `manifests_dir` variable (`mvp/tools/check_manifest_append_only.py:453-454`), the real work:**
```python
manifests_dir = registry_abs / MANIFESTS_DIR_NAME
manifests_rel = manifests_dir.relative_to(top).as_posix()
```
This one variable is then threaded through the rest of `check_append_only` (lines 453-602): the tracked/staged listing for Rule 5 (458-486), `_non_regular_entries(manifests_dir, manifests_rel)` for Rule 6 (568), and the `manifests_dir.glob("**/*.json")` walk for Rule 4's one-sha256-per-partition-path check (577-598). **Generalizing this means looping the body of `check_append_only` over each name in `REGISTRY_DIR_NAMES` (or refactoring rules 4/5/6 into a per-directory helper called once per registry subdirectory)** — a materially larger change than the `_is_manifest_shaped` fix, and RESEARCH.md Q1 already flags the open question of whether Rule 5's vacuity check should be evaluated per-directory or across all of them combined. **The plan must decide and state which; do not let the planner assume this is a one-line diff.**

**The vacuity trap to avoid regardless of which shape is chosen** — Rule 5's exact refusal text (`mvp/tools/check_manifest_append_only.py:482-486`):
```python
if not tracked and not staged:
    errors.append(
        f"HEAD and the index both track 0 manifests under {manifests_rel}/ "
        "-- refusing a vacuous pass"
    )
```
This fires (per-directory, once the loop exists) the moment `segments/` is added to the protected set and nothing has been committed there yet — **the guardrail-extension commit and the first committed segment manifest (and errata manifest) must land in the same commit**, or CI goes red with this exact message.

Constant rename to `REGISTRY_DIR_NAMES = frozenset({"manifests", "segments", "errata"})` per RESEARCH.md Q1's recommendation; `NON_REGISTRY_COMPONENTS`/`_realm` (`mvp/tools/check_manifest_append_only.py:243-289`) need **no change** — they already operate generically on path components.

---

### `mvp/tools/check_manifest_id_integrity.py` (extend the glob root)

**Analog:** itself. Current single-directory glob (`mvp/tools/check_manifest_id_integrity.py:55-63`):
```python
def _iter_manifest_files(registry_root: Path) -> list[Path]:
    manifests_dir = Path(registry_root) / "manifests"
    if not manifests_dir.exists():
        return []
    return [
        p for p in manifests_dir.glob("**/*.json")
        if BY_DATE_MARKER not in f"/{p.relative_to(manifests_dir)}"
    ]
```
Generalize to iterate `{"manifests", "segments", "errata"}` (no `partitions`-key access anywhere in this file — RESEARCH.md Q1 confirms it is safe to extend as-is, low risk — this file's whole logic is `json.loads` + `compute_manifest_id` + a filename/field comparison, unlike `check_manifest_append_only`'s multi-rule body). The vacuous-scan guard to preserve (`mvp/tools/check_manifest_id_integrity.py:90-99`):
```python
if not manifest_files:
    print(f"FAIL: found 0 manifest(s) under {...} -- a scan that checked nothing must not pass")
    return 1
```

---

### `mvp/tools/check_no_manifest_rewrite.py` — NOT extended (recorded for the planner)

**Why it must stay pointed at `manifests/` only** (`mvp/tools/check_no_manifest_rewrite.py:200-202`, verified):
```python
if not manifest.get("partitions"):
    all_bad.append((manifest_label, "<no partitions: verifies nothing>"))
```
Every segment/errata manifest is, by design, exactly this shape — pointing this tool at `segments/`/`errata/` would fail every one of them by construction. **Do not touch this file.**

---

### `mvp/tools/check_harness_accessor_only.py` (new static tripwire, D-05-15)

**Analog:** `mvp/tools/check_single_feature_path.py` in full — its "N sanctioned callers, everything else flagged" shape fits D-05-15 more closely than `check_lockbox_containment.py`'s "whole module, many rule types" shape (RESEARCH.md Q10's explicit recommendation). **Two gaps must be resolved before copying the template, not discovered in review:**

**Gap 1 — name collision.** `load_features` is not unique in this codebase. There are **two unrelated functions with the same bare name**:
```python
# spec/catalogue.py:86 — the CATALOGUE loader, unrelated to D-05-15
def load_features(path: Path = FEATURES_TOML) -> dict[str, FeatureEntry]: ...

# features/tier.py:473 — the gated accessor D-05-15 actually means
def load_features(manifest_id: str, dataset: str, *, registry_root: Path, lake_root: Path) -> pl.DataFrame: ...
```
`tools/check_spec_diff.py`, `tools/check_catalogue_completeness.py`, and `spec/render.py` all import `spec.catalogue.load_features` — a scanner watching the bare name `"load_features"` would flag these immediately, and the project already knows about the collision: `tests/features/test_tier.py:32` explicitly aliases `from spec.catalogue import load_features as load_catalogue_features` to keep the two apart. **`WATCHED_MODULES` (or the scanner's equivalent) must key on the dotted path `features.tier.load_features`, not the bare name** — `check_single_feature_path.py`'s existing `_is_watched`/combined-name logic (the `ImportFrom` branch that builds `f"{module}.{alias.name}"`) already does this correctly for dotted module+name pairs; just point it at the right dotted string.

**Gap 2 — no `ast.Attribute` walk.** `check_single_feature_path.py`'s `scan_source` (lines 184-249, read in full) only inspects `ast.Import`, `ast.ImportFrom`, and `ast.Call` (for `importlib.import_module`/`__import__` literals). It has **no** handling for `import features.tier as t; t.load_features(...)` — a module-alias import followed by attribute access. This is a real gap in the template itself, not specific to the harness: state it explicitly in the new scanner's docstring (following the existing file's own honesty convention — "IT DOES NOT CLAIM TO STOP A DETERMINED IMPORT") rather than silently inheriting it. The planner should decide: accept-and-document (matches the existing tolerance for `importlib.import_module` with a dynamically-built string), or add an `ast.Attribute` walk that flags `<alias>.load_features` when `<alias>` is bound to `features.tier`.

**Confirmed current callers, to seed the sanctioned list** — grepped this session (`grep -rln "load_features" mvp --include="*.py" | grep -v tests`): only `features/tier.py` (the definition) references the tier's `load_features` outside tests; `data/store.py:90` and `features/normalize.py:499` mention it only in comments/docstrings, not imports. **No non-test, non-harness file imports `features.tier.load_features` today** — the sanctioned list can start as `{"harness/accessor.py"} ∪ {the four test files D-05-15 names}`, and the scanner should be green from the day it lands, before the harness even exists to populate the sanctioned side. Confirm the exact four test files at plan time (`tests/store/test_features_tier_containment.py`, `tests/features/test_tier.py`, and `tests/features/test_holdout_refusal.py` are three confirmed importers of `features.tier.load_features`; find the fourth by grepping `from features.tier import` `-l` `mvp/tests`).

**Structure to copy nearly verbatim** (`mvp/tools/check_single_feature_path.py:64-92`):
```python
WATCHED_MODULES: frozenset[str] = frozenset({"features.kernel", "features.reference"})

SANCTIONED_FILES: dict[str, str] = {
    "features/api.py": "THE entry point ...",
    "features/kernel.py": "the @njit implementation itself ...",
    "features/reference.py": "the readable twin ...",
    "tests": "the equivalence, leakage and kernel-state tests must name both implementations ...",
}
```
For `check_harness_accessor_only.py`, watch the specific dotted target (`"features.tier.load_features"`, matched via the combined-name branch, per Gap 1), and `SANCTIONED_FILES` names `mvp/harness/accessor.py` plus the four sanctioned test files, listed individually (D-05-15 says "the four sanctioned test files" — a `dict[str, str]` with one reason each, same shape as the template, not a bare directory sanction).

**AST-walk shape to copy verbatim otherwise** (`Import`/`ImportFrom`/dynamic-`Call` detection, `mvp/tools/check_single_feature_path.py:184-249`) — same three node types, same `_absolute_module` relative-import resolution, same explicit non-goal:
```python
"""... IT DOES NOT CLAIM TO STOP A DETERMINED IMPORT. A dynamically
constructed module name (`importlib.import_module("features." + "kernel")`)
is NOT detected, by design ..."""
```
`check_harness_accessor_only.py`'s own docstring must repeat this same honest disclaimer (extended to name Gap 2 above too), naming the runtime control it defers to.

**Its self-test's shape to copy** — `mvp/tests/tools/test_check_single_feature_path.py`, especially the anti-vacuity test:
```python
def test_static_scan_states_what_it_cannot_see():
    ...
    docstring = (PKG_ROOT / "tools" / "check_single_feature_path.py").read_text()
    assert "dynamically" in docstring
    assert "test_three_call_sites_are_byte_identical" in docstring, (
        "the scanner must name the runtime control it defers to"
    )
```
and:
```python
def test_the_scan_is_green_over_the_whole_package(capsys):
    assert main() == 0
    out = capsys.readouterr().out
    assert "scanned" in out
    scanned = int(out.split("scanned ")[1].split(" ")[0])
    assert scanned > 100, f"only {scanned} files scanned -- a vacuous pass"
```

---

### `mvp/tests/harness/` (new root, NO `__init__.py`)

**Analog:** `mvp/tests/tracking/` and `mvp/tests/lockbox/` — same no-`__init__.py` posture. Confirmed no collision exists yet (`ls mvp | grep -i harness` and `ls mvp/tests | grep -i harness` both empty, per RESEARCH.md Q9/Q11) — apply the three-times-learned rule proactively.

**`conftest.py` pattern — bare `tmp_path`, no shared fixture object** (`mvp/tests/tracking/test_mlflow_utils.py`, confirmed pattern via grep):
```python
def test_missing_mandatory_tag_raises_before_start_run(tmp_path):
    ...
    with pytest.raises(MissingTagError):
        start_tracked_run(str(tmp_path), incomplete_tags, "test-experiment", min_free_gb=0.0)
```

**Segment/feature-partition builder — do NOT copy `feature_frame`'s `etime` line, and do NOT build segment fixtures through the curated-tier helper.** `mvp/tests/fixtures/feature_tier.py:38-57`'s `feature_frame` generates:
```python
data["etime"] = [1_000 * (i + 1) for i in range(rows)]   # a 3-row, 3-microsecond span
```
Against `h_max = 600 s` (the purge horizon) or even `FOLD_EMBARGO_NS ≈ 1 s`, every purge/embargo invariant test built on this span passes **vacuously** — the whole fixture is already inside (or already outside) any real gap, so shrinking the gap to 0 changes nothing observable. Copy the *schema-driven column loop* from `feature_frame`, but replace the `etime` line with a parameterized `(start_ns, step_ns, rows)` generator, and validate the anti-vacuity property (RESEARCH.md Q9: "at least ~1–2 hours of synthetic `etime` with a quote every 1–5 seconds").

Also: `feature_tier.py`'s `issue_curated_day` (lines 74-107) issues a **curated**-tier manifest (`tier="curated"`), but `harness/accessor.py` and `load_features` both resolve `expected_tier=FEATURES_TIER`. Harness fixtures that need a real, loadable features-tier manifest must go through the actual writer path instead:
```python
# features/tier.py:239 — write_feature_partition(df, *, lake_root, symbol, date, registry_root=None, commit=True) -> dict
# features/tier.py:374 — issue_feature_manifest(*, symbol, date, partition_entry, curated_manifests, code_hash, registry_root) -> dict
```
both already read in full this session — `issue_feature_manifest` is thin, delegating straight to `data.store.issue_manifest` with `tier=FEATURES_TIER`, `dataset=f"{symbol}.{FEATURES_TIER}"`.

**The closer precedent for realistic multi-row, real-span synthetic data is `mvp/tests/fixtures/feature_build.py`** (confirmed by reading its header and `quote_frame`, not just RESEARCH.md's mention): it anchors etimes to a real UTC midnight (`DAY_START = 1_789_257_600 * NS_PER_SECOND`) specifically "so 'a label whose horizon runs past the end of day D' is the real thing" — the same anti-vacuity discipline the 5-segment and k-fold fixtures need, already solved once in this codebase. Read `quote_frame`/`trade_frame`/`seed_day`/`seed_two_days` in full at plan time before writing a new generator from scratch.

**For lockbox-tier fixtures specifically** (needed by `holdout_declare.py`'s tests and possibly `data/lockbox.py`'s new function's tests), copy `_build_segment` wholesale (`mvp/tests/lockbox/test_token_one_look.py:72-118`, full function already quoted above under `data/lockbox.py`'s entry).

**`write_holdout_registry`** (`mvp/tests/fixtures/feature_tier.py:139-157`, quoted above under `data/holdout.py`) is directly reusable as-is for any harness test needing a declared or malformed holdout registry.

---

## Shared Patterns

### MLflow-first durable counter / stamp-before-read
**Source:** `mvp/data/lockbox.py:_mlflow_has_consumed`, `_require_canonical_tracking_root`, `_require_initialised_mlflow_store` (lines 176-312)
**Apply to:** `mvp/harness/budget.py`, `mvp/harness/negative_log.py`
```python
# Order that must never be reordered:
# 1. Query MLflow FIRST (durable). Exception propagates unmodified.
# 2. Refuse an uninitialized/wrong-root store BEFORE constructing any client.
# 3. Stamp/record BEFORE returning any result to the caller.
```

### Content-addressed registry write (no `partitions` key)
**Source:** `mvp/data/store.py:140-151` (`canonicalize_manifest`/`compute_manifest_id`, import directly) + `297-301` (`_atomic_write_json`, DUPLICATE rather than import — see `segments.py` entry for why)
**Apply to:** `mvp/harness/segments.py`, the `errata/` registry writer, `data/holdout.py`'s new writer

### Fail-closed registry read (absent ≠ empty; malformed raises)
**Source:** `mvp/data/holdout.py:_registry_problem`, `quarantined_dates` (lines 106-184)
**Apply to:** any harness code reading `segments/`, `errata/`, or `holdout.json`
```python
if not path.exists():
    return QuarantinedDates((), declared=False)   # absent, logged once
...
problem = _registry_problem(body, symbol=symbol)
if problem is not None:
    raise ValueError(f"{path}: {problem}")          # malformed -> raise, never silently empty
```

### Ordered-gates accessor
**Source:** `mvp/features/tier.py:load_features` (lines 473-536)
**Apply to:** `mvp/harness/accessor.py`
```
1. integrity/resolve first (never let a bad hash reach business logic)
2. hard refusals (holdout / held_out role) — unconditional, before any masking
3. policy exclusions (DQ pause / row-admission / errata masking)
4. provenance logging, then the actual read/count
```

### AST-walk static tripwire, N-sanctioned-callers shape
**Source:** `mvp/tools/check_single_feature_path.py` (whole file, 287 lines)
**Apply to:** `mvp/tools/check_harness_accessor_only.py`
- `WATCHED_MODULES`/watched dotted target — **must be the dotted path `features.tier.load_features`, not the bare name `load_features`** (name-collision gap, see that entry)
- `SANCTIONED_FILES` (dict: file or top-level dir → reason)
- `scan_source` walking `ast.Import`/`ast.ImportFrom`/`ast.Call` (dynamic import literal only) — **has no `ast.Attribute` handling; that gap must be stated, not silently inherited** (see that entry)
- `main()` prints `scanned N files`, fails on `scanned == 0` (anti-vacuity) and on any violation

### Guardrail hook parity — byte-identical `entry:`/`run:`, no `files:` scoping to worry about
**Source:** `.pre-commit-config.yaml` lines 130-143 (existing `check_manifest_id_integrity`, `check_manifest_append_only` entries) mirrored at `.github/workflows/ci.yml` lines 72-77; verified by `mvp/tests/tools/test_ci_pre_commit_parity.py`
**Apply to:** the new `check_harness_accessor_only` hook, and the (unchanged-command, just now covering more files) `check_manifest_append_only`/`check_manifest_id_integrity` hooks

Confirmed by reading `.pre-commit-config.yaml:120-150`: every one of these hooks is `language: system`, `pass_filenames: false`, with **no `files:` regex** — the command runs unconditionally and does its own path discovery internally (`LAKE_REGISTRY_ROOT`-relative globbing inside the tool), so extending a tool's internal glob roots to cover `segments/`/`errata/` needs **no YAML change beyond the new hook's own `entry:`/`run:` pair**.
```yaml
# .pre-commit-config.yaml
- id: check-harness-accessor-only
  name: check_harness_accessor_only
  entry: uv run --locked --directory mvp python -m tools.check_harness_accessor_only
  language: system
  pass_filenames: false
```
```yaml
# .github/workflows/ci.yml
- name: check_harness_accessor_only
  run: uv run --locked --directory mvp python -m tools.check_harness_accessor_only
```
`test_ci_pre_commit_parity.py`'s exact parity test (`mvp/tests/tools/test_ci_pre_commit_parity.py:54-63`):
```python
def test_every_pre_commit_command_is_a_ci_step_verbatim():
    hooks = _commands(PRE_COMMIT, "entry")
    steps = set(_commands(CI, "run"))
    assert len(hooks) >= 15, f"only {len(hooks)} hook commands found -- parser drift?"
    missing = [c for c in hooks if c not in steps and c not in PRE_COMMIT_ONLY]
    assert not missing, (
        "pre-commit hook command(s) with no byte-identical CI step -- a gate "
        f"that exists in one caller only: {missing}"
    )
```
`len(hooks) >= 15` is a **floor**, not a target — it already passes today (18 hooks) and adding a 19th needs no change to this assertion. The requirement that matters for the new hook is simply: its `entry:` string and its `run:` string must be byte-identical.

### Mandatory MLflow tags — do not touch, only fill real values
**Source:** `mvp/tracking/mlflow_utils.py:58-69` (`MANDATORY_TAG_KEYS`, unchanged) + `198-245` (`start_tracked_run`)
**Apply to:** every harness module that starts a tracked run (`accessor.py`'s look, `budget.py`'s access run, `negative_log.py`'s failed-config run)
```python
MANDATORY_TAG_KEYS: frozenset[str] = frozenset({
    "code_hash", "data_hash", "seed", "env_hash",
    "segment_manifest_id", "model_class", "fold_config", "stage",
})
```
`segment_manifest_id`/`fold_config`/`stage` stop being `"n/a"` placeholders once the harness runs; `segment_name` (D-05-13's budget granularity) is an **additive** tag via the same `set_tags`-after-creation pattern `log_data_provenance` already uses (`mvp/tracking/mlflow_utils.py:248-304`) — never added to `MANDATORY_TAG_KEYS` itself.

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| `mvp/harness/kfold.py` — the purge+embargo block-partitioning algorithm itself | utility | batch | Genuinely new algorithm; CLAUDE.md explicitly mandates hand-rolling over skfolio/mlfinlab/timeseriescv. Use the row-selection idiom and invariant-test discipline from existing code (documented above), but the block-generation logic has no prior implementation anywhere in this codebase. Planner should budget real design time here, not just adaptation. |
| `mvp/harness/row_admission.py` — the stale-book age computation | utility | transform | The exact polars expression was measured and verified this session (RESEARCH.md Q7) but never previously committed as a named function; treat the verified expression (given above) as the reference implementation, not a file to diff against. |

## Metadata

**Analog search scope:** `mvp/data/`, `mvp/features/`, `mvp/tools/`, `mvp/tracking/`, `mvp/spec/`, `mvp/tests/{fixtures,tracking,lockbox,tools,features,store}/`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`
**Files read this session:** `data/holdout.py` (full), `data/lockbox.py` (full), `data/store.py` (targeted: manifest/hash/path functions), `features/tier.py` (targeted: `write_feature_partition`, `curated_manifest_input`, `issue_feature_manifest`, `assert_buildable`, `load_features`/`refused_dates_for`), `tools/check_manifest_id_integrity.py` (full), `tools/check_manifest_append_only.py` (targeted: `MANIFESTS_DIR_NAME` all three use sites, `_is_manifest_shaped`, `_head_manifest_blobs`/`_worktree_manifest_blobs`, `NON_REGISTRY_COMPONENTS`, `check_append_only`'s Rules 2-6 and its vacuity rule), `tools/check_no_manifest_rewrite.py` (targeted: the partitions-check that must not be extended), `tools/check_single_feature_path.py` (full), `tools/check_lockbox_containment.py` (targeted: sanctioned-names section), `tracking/mlflow_utils.py` (full), `tests/fixtures/feature_tier.py` (full), `tests/fixtures/feature_build.py` (targeted: header + `quote_frame`), `tests/tools/test_check_single_feature_path.py` (full), `tests/tools/test_ci_pre_commit_parity.py` (full), `tests/lockbox/test_token_one_look.py` (targeted: `_build_segment` + imports), `tests/tracking/test_mlflow_utils.py` (targeted: `tmp_path` pattern), `tests/tools/test_check_manifest_append_only.py` (targeted: header + fixture helpers), `.pre-commit-config.yaml` (targeted: lines 120-150, confirmed no `files:` scoping) / `.github/workflows/ci.yml` (grepped for existing hook/step lines), plus a repo-wide grep for `load_features` callers to resolve the name-collision gap (`spec/catalogue.py`, `tools/check_spec_diff.py`, `tools/check_catalogue_completeness.py`, `spec/render.py`, `tests/features/test_tier.py`'s explicit aliasing)
**Pattern extraction date:** 2026-09-20

## PATTERN MAPPING COMPLETE

**Phase:** 5 - Fold Harness & Overfitting Controls
**Files classified:** 21
**Analogs found:** 21 / 21 (2 flagged as "no direct analog" for the specific new-algorithm portions; every other file has a named, line-cited precedent; 2 of the exact matches carry a flagged gap the planner must resolve, not silently inherit — the `check_manifest_append_only` scope of change, and the `load_features` name-collision / `ast.Attribute` gap in the new tripwire)

### Coverage
- Files with exact analog: 15
- Files with role-match analog: 4
- Files with partial/no analog (new algorithm): 2

### Key Patterns Identified
- Every registry writer in this phase (`segments.py`, the `errata/` writer, the new `holdout.py` writer) reuses `data.store.canonicalize_manifest`/`compute_manifest_id` (import directly) and a **duplicated**, not imported, `_atomic_write_json` (the project's own stated norm for crossing a module boundary with a private helper) — never `issue_manifest`, which hard-refuses an empty `partitions` list.
- Every durable-counter/log (`budget.py`, `negative_log.py`) copies `data.lockbox`'s exact MLflow-first, stamp-before-read, exception-propagates shape — the same three helper functions, same query cost profile (0.0088 s at 50 runs, measured).
- `harness/accessor.py` extends `features/tier.py:load_features`'s four-ordered-gates shape by exactly one gate (budget check), keeping integrity-first ordering.
- The static tripwire (`check_harness_accessor_only.py`) should clone `check_single_feature_path.py`'s "N named sanctioned callers" shape — but must watch the dotted path `features.tier.load_features`, not the bare name (a second, unrelated `load_features` already exists in `spec/catalogue.py` and is imported by three non-test files today), and must either accept-and-document or close the template's missing `ast.Attribute`-walk gap.
- `check_no_manifest_rewrite.py` is the one existing guardrail that must NOT be touched — pointing it at `segments/` would fail every segment manifest by design.
- Extending `check_manifest_append_only.py` is two different-sized changes: `_is_manifest_shaped` generalizes in one line, but `check_append_only()`'s single `manifests_dir` variable threads through Rules 4/5/6 and needs an actual per-directory loop (or an explicit, planner-stated decision on how Rule 5's vacuity check scopes across multiple registry directories) — plus the guardrail-extension commit and the first committed `segments/`/`errata/` manifest must land in the **same commit**.
- Lockbox-tier manifests plausibly reuse the **same dataset name** as their source stream (`"BTCUSDT.trade"` confirmed for trade; `"BTCUSDT.features"` a natural but unconfirmed extrapolation) — the planner should treat this as a recommendation to verify, not a settled fact, since no fixture issues a lockbox-tier features manifest today.
- Fixture spans must NOT copy `feature_tier.py:feature_frame`'s 3-microsecond synthetic `etime` line — that span makes every purge/embargo invariant test pass vacuously against a 600 s horizon. `feature_build.py`'s real-UTC-midnight-anchored `quote_frame` is the closer precedent, and any features-tier-resolvable fixture must go through `write_feature_partition`/`issue_feature_manifest` (tier `FEATURES_TIER`), not `feature_tier.py`'s curated-tier `issue_curated_day`.
- No hook needs a `.pre-commit-config.yaml`/`ci.yml` `files:` regex edit — confirmed none of the manifest-guardrail hooks use `files:` scoping; they self-discover paths internally.

### File Created
`/Volumes/ProjectsSSD/aihedgefund/repo/.planning/phases/05-fold-harness-overfitting-controls/05-PATTERNS.md`

### Ready for Planning
Pattern mapping complete. Planner can now reference analog patterns in PLAN.md files.
