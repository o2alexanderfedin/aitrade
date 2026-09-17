---
phase: 03-data-layer-backfill-ingest-lockbox
verified: 2026-09-16T15:30:00Z
status: passed
human_verification_resolved: "2026-09-16 — user chose to improve rather than accept T-03-09: CI now runs check_manifest_id_integrity on every committed manifest and check_no_manifest_rewrite --full against a committed fixture lake (red-proved); plus check_manifest_append_only over full history."
score: 5/5 must-haves verified (1 WARNING requiring a human disposition decision)
overrides_applied: 0
human_verification:
  - test: "Confirm the disposition of T-03-09: the CI leg of tools/check_no_manifest_rewrite.py --full is structurally inert (always prints SKIP and exits 0) because DEFAULT_LAKE_ROOT is a hardcoded /Volumes/ProjectsSSD/... path with no override and GitHub Actions runners never have that volume mounted. The real byte-level immutability check only ever runs (a) on this machine's pre-push hook, or (b) is not covered."
    expected: "Either: accept as documented (single-developer/single-machine project, CI cannot physically reach the SSD, real guarantee comes from resolve_manifest's sha256-on-every-read plus the local pre-push hook) -- or schedule the minimal fix (CI-only manifest self-consistency check: recompute manifest_id from body for every committed JSON, which needs no mounted lake) as a follow-up item."
    why_human: "This is a scope/risk-acceptance decision (is a guardrail that can never fire for real in the one place that runs on every push, for every contributor, an acceptable design given the physical constraint, or does it need the cheaper CI-native check added), not a code-correctness question -- the code does exactly what its docstring says it does."
---

# Phase 3: Data Layer — Backfill, Ingest & Lockbox Verification Report

**Phase Goal:** A canonical, immutable, manifest-addressed data lake exists with quality gates
and a mechanically enforced held-out quarantine.
**Verified:** 2026-09-16T15:30:00Z
**Status:** passed (human item T-03-09 resolved 2026-09-16 — see frontmatter)
**Re-verification:** No — initial verification

All evidence below was gathered by me, directly against the real mounted lake
(`/Volumes/ProjectsSSD/aihedgefund/lake/`) and the real repo on
`feature/phase-03-data-layer-backfill-ingest-lockbox`, not read from SUMMARY.md claims. Every
command is reproducible from `/Volumes/ProjectsSSD/aihedgefund/repo/mvp`.

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | ~3 months of trades backfilled with per-dataset unit registry; timestamps pass the plausibility gate despite format heterogeneity | ✓ VERIFIED | `lake/raw/.../source=archive/` holds 107 days (2026-06-01→09-15). `spec/unit_registry.toml` has a real, measured `futures-um.trades` entry (`header=true, time_unit=ms`) plus a declared-but-unused `spot.trades` row so `UnitRegistryError` on an unknown pair is actually exercised (`data/unit_registry.py:get_unit_entry`). The "CI plausibility gate" is DQ check (6), `check_etime_plausibility` (`data/dq/checks.py`), deliberately folded into the DQ module rather than a standalone `ci.yml` step (03-RESEARCH.md:119 — this is a documented design decision, not a missing artifact). I read `lake/dq/date=2026-07-14/report.parquet` myself: `etime_plausibility, dq_status=ok`. Across all 111 real manifests the check found **0** violations (confirmed in `03-04-SUMMARY.md`'s transcript and structurally guaranteed by the check reading `manifest["etime_range"]`, itself computed at issuance time from the real converted data). |
| 2 | Legacy `tradeSide=0` rows classified by nearest L1 quote; `tradeSide_raw`/`tradeSide_corrected` stored side by side | ✓ VERIFIED | `data/ingest/trade_side.py:resolve_side` adds all three columns; wired into `curated_build.py:299` (`chosen_df = resolve_side(chosen_df)`). I read the real `BTCUSDT.trade/2026-09-14` curated partition myself: 3,688,572 rows, `side_method` is 100% `exact_flag` (0 `nearest_quote`/`unknown`), matching PROBE-RESULTS.md's finding that this dataset carries an exact flag on every row. The classifier itself (`nearest_quote_side`) is real, non-stub code (`join_asof(strategy="backward", allow_exact_matches=False)`, with a documented, measured fix for same-millisecond leakage) and is unit-tested against the committed real-row fixture (`tests/fixtures/side_convention_rows.py`). |
| 3 | Lake partitioned by symbol/date, versioned schema, immutable + manifest-addressed; `(etime, seq)` deterministic | ✓ VERIFIED, ⚠️ WARNING on CI enforcement | See "RP-1 red-proof, my own" and "(etime, seq) determinism at scale" below. |
| 4 | Daily DQ report exists; degradation pauses training until acknowledged | ✓ VERIFIED | See "DQ pause mechanism" below. |
| 5 | Lockbox quarantine physically unreadable by default; unlock requires a token, logged to MLflow as a one-look annotation | ✓ VERIFIED | See "Lockbox" below. |

**Score:** 5/5 truths verified as designed and built; 1 carries a disclosed architectural
limitation (WARNING) that needs a human disposition decision before the phase closes cleanly.

---

### RP-1 red-proof, my own (immutability guardrail — genuinely wired)

This is the one red-proof the orchestrator had not personally verified. I ran it myself against
the real, mounted, committed lake — not a `tmp_path` fixture.

```
$ cp .../lake/curated/.../bookTicker/date=2026-09-14/part-1789594267178664000.parquet /tmp/rp1_backup.parquet
$ shasum -a 256 <target>
d597a405e5a43d30316182f052f8381724ad5dd741f050297615865eb2bcbcd7  <target>
$ printf 'X' >> <target>                      # append one byte
$ ./.venv/bin/python3 -m tools.check_no_manifest_rewrite            # fast (mtime+size)
FAIL: partition(s) diverged from their manifest:
  data/lake_registry/manifests/BTCUSDT.bookTicker/456cf9....json -> curated/.../part-1789594267178664000.parquet
$ ./.venv/bin/python3 -m tools.check_no_manifest_rewrite --full     # sha256
FAIL: partition(s) diverged from their manifest:  (same path)
$ cp /tmp/rp1_backup.parquet <target>          # restore
$ shasum -a 256 <target>
d597a405e5a43d30316182f052f8381724ad5dd741f050297615865eb2bcbcd7  <target>   # identical
$ ./.venv/bin/python3 -m tools.check_no_manifest_rewrite --full
checked 111 manifest(s), mode=full (sha256)     # clean, no FAIL
```

Both directions confirmed, against a real committed 498 MB partition, not a fixture. I also
confirmed the pre-push git hook that runs this in `--full` mode is genuinely installed
(`.git/hooks/pre-push` is a real pre-commit-generated hook, not a stub) and that the same command
string appears byte-identical in `.pre-commit-config.yaml` (`stages: [pre-push]`) and
`.github/workflows/ci.yml`.

**However** — reading `tools/check_no_manifest_rewrite.py`'s own `main()`: `DEFAULT_LAKE_ROOT` is
hardcoded (`data/lake_paths.py:36`, `"/Volumes/ProjectsSSD/aihedgefund/lake"`), with **no**
environment-variable or CLI override. `main()` does `if not lake_root_path.exists(): print("SKIP:
... not mounted"); return 0`. A GitHub Actions `ubuntu-latest` runner will **never** have
`/Volumes/ProjectsSSD` mounted. This means the CI leg of this guardrail — the leg that fires on
every single push, from any contributor's or agent's machine, regardless of local hook
installation — is **structurally incapable of ever performing a real check**. It will print `SKIP`
and exit 0 on every real CI run, forever, by construction, not by bug.

This is disclosed, not hidden: the module's own docstring documents "T-03-09, an honest,
disposition=accept blind spot," and `03-02-SUMMARY.md:211` states it explicitly ("`SKIP` is
exercised in CI, where it never is"). The load-bearing guarantee that survives regardless —
`resolve_manifest`'s stored-hash-vs-on-disk-hash check inside `load_curated`, which runs on
**every read**, everywhere, always — is real and I verified it separately (RP-2 was already
red-proved by the orchestrator; I additionally confirmed `resolve_manifest` raises
`ManifestHashMismatch` and never a silent wrong-data return by reading `data/store.py:216-234`
directly).

**Practical consequence:** a `git push --no-verify`, or a push from any clone that never ran
`pre-commit install --hook-type pre-push`, silently bypasses the one component that is
specifically supposed to catch a mutated partition **before** it becomes visible to anyone else —
and CI will not catch it either. `resolve_manifest`'s per-read check (RP-2) still catches it the
next time anyone actually *loads* that partition, so no bad data can be silently consumed — but
the "mechanically enforced… in CI" framing implied by RP-1's task description is not fully true
for the CI leg specifically.

**This looks like an accepted, disclosed limitation, not an oversight** — but it is exactly the
kind of gap this project's own history says gates catch. Flagged as `human_needed` rather than a
BLOCKER because: (a) it is explicitly documented with an honest, named disposition, (b) the
stronger, always-on guarantee (per-read hash verification) is real and independently verified,
and (c) the physical constraint (cloud CI cannot reach a local SSD with multi-GB files that are
deliberately kept out of git) has no obvious full fix — only a narrower one (see below).

**Minimal concrete fix, if the disposition is "improve rather than accept":** `main()` can add a
CI-safe leg that recomputes `manifest_id` from each committed manifest's own body (exactly what
`resolve_manifest` already does at `data/store.py:216-219`) for every manifest under
`LAKE_REGISTRY_ROOT`, unconditionally — this needs no mounted lake, since manifest JSON is
git-committed, and it catches a hand-edited/corrupted manifest (a real, CI-checkable class of
tampering) even though it still cannot catch a mutated *partition file* from CI. Partition-hash
verification stays a local-only guarantee, but at least CI stops being a guardrail that can never
find anything.

---

### `(etime, seq)` determinism at real scale (my own proof, not the hypothesis fixture)

```python
df = pl.concat([pl.read_parquet(p) for p in glob(".../BTCUSDT/stream=trade/date=2026-09-14/*.parquet")])
# 3,688,572 rows
base = df.drop("seq")
out1 = materialize_seq(base, ["etime", "trade_id"])
out2 = materialize_seq(base.sample(fraction=1.0, shuffle=True, seed=42), ["etime", "trade_id"])
out1.equals(out2)                     # -> True
sha256(out1.write_parquet()) == sha256(out2.write_parquet())
# 16f933cfa78e67eec8d249df413b4949d07970521a4b8d7e7b0c6d9cd7216c3e == same, both directions
```

Confirmed order-independent at real multi-million-row scale, not just over the hypothesis
fixture — `materialize_seq`'s `df.sort(sort_keys).with_row_index("seq")` is a pure function of
the sort key columns (last key is asserted unique before sorting), so this is also provably, not
just empirically, order-independent — my test corroborates the algebraic argument.

---

### DQ pause mechanism (real data, my own scan)

I concatenated every real `report.parquet` (107 dates, 444 rows total) and found exactly 10
`failed`/`degraded` rows, spanning exactly 6 `(symbol, stream, date)` combinations:
`(trade,09-12)`, `(bookTicker,09-12)`, `(trade,09-14)`, `(bookTicker,09-14)`, `(trade,09-15)`,
`(bookTicker,09-15)`. `mvp/data/lake_registry/dq_acknowledgements/` contains exactly 6 files,
matching those 6 combinations by filename. No unacknowledged failed/degraded day exists in the
real range.

- Checks (2) reconciliation and (3) na_placeholder genuinely read persisted `build_stats.json`
  fields (`data/dq/checks.py:check_reconciliation`/`check_na_placeholder` take a `build_stats:
  dict` argument, never touch curated Parquet) — confirms Plan 04's correction landed in code,
  not just in the plan text.
- Fail-closed on absence confirmed two ways: (a) `tests/dq/test_pause_enforcement.py`'s Case 2 and
  the `test_all_n_a_report_is_treated_as_missing_not_ok` test both pass; (b) I read the real
  `lake/dq/date=2026-07-14/report.parquet` myself — it contains **zero rows** for `bookTicker`
  (not a false `"ok"`, not even a literal `"n/a"` row — `report.py:build_report_rows_for_date`
  simply never calls the bookTicker checks when no manifest exists for that date/stream).
  `data/store.py:_dq_status_for_date` treats `rows.height == 0` as `"missing"`, which
  `_enforce_dq_pause` treats identically to `"failed"` — **more conservative** than an explicit
  `"n/a"` row would be, and correctly so; hunt item 5 ("is a day with no data silently `ok`?") is
  answered: no.

**WARNING, latent (not currently triggered by real data):** `check_l1_sparsity` returns
`{"dq_status": "ok", "value_seconds": 0.0}` when `etimes.len() < 2` (`data/dq/checks.py`,
`check_l1_sparsity`). A bookTicker partition with 0 or 1 row (e.g. a day capture ran for one
second before crashing) would report sparsity as `"ok"` rather than `"n/a"`/`"failed"`. This is
not hit by any of the 4 real L1 days (all are multi-million-row), so it is not a present failure
of criterion 4, but it is a concrete example of "an assertion that can never fail given how its
input is computed" for a not-yet-observed input shape. Minimal fix: return `"n/a"` (or `"failed"`)
when `etimes.len() < 2`, mirroring the caller's existing "n/a instead of calling at all" intent
stated in the function's own docstring but not actually implemented for this edge.

**INFO:** `check_crossed_locked_book` always returns `dq_status="ok"` — by design (03-CONTEXT.md
declares check (4) informational, no threshold), not a bug.

**INFO — `rtime` joins something:** `resync_windows_for_date` (checks.py) returns
`gap_start_rtime`/`gap_end_rtime`/`warmup_end_rtime` that Phase 4 is documented to join against
curated `etime` directly (checks.py module docstring; 03-CONTEXT.md). This is a literal, disclosed
deviation from the hard constraint "`rtime` joins nothing" — justified because a capture outage by
definition has no `etime` to key on — but Phase 4 planning must not treat the resulting boundary
as etime-exact; flag for that phase's own verification.

---

### Lockbox (DATA-08)

- `ls -ld lake/lockbox` → `d---------` (chmod 0000), confirmed live on the real mounted lake.
- `tools/check_lockbox_containment.py` is a genuine AST scan (walks every `ast.Constant`
  string/bytes literal + `ImportFrom` node for private-name imports), not a grep — ran it myself:
  `scanned 47 files`, 0 violations. Unlike `check_no_manifest_rewrite`, this guardrail has **no**
  lake-mount dependency, so its CI leg is genuinely live on every push (no SKIP branch exists in
  its `main()`).
- `data/store.py` contains zero occurrences of `"lockbox"` anywhere — confirmed by direct grep —
  matching the "no code path, not a flag, not a default argument" claim.
- `data/lockbox.py:_mlflow_has_consumed` uses `MlflowClient().search_runs(...)`, never the banned
  pandas-returning `mlflow.search_runs()` module function — confirmed by reading the code and by
  the module's own docstring citing the spec.md DON'T.
- RP-3's independence check (`test_load_curated_against_lockbox_manifest_is_blocked_by_chmod`)
  constructs a fixture manifest with a `lockbox/`-prefixed path and calls `data.store.load_curated`
  **directly**, bypassing the missing-code-path guardrail on purpose, and asserts
  `PermissionError` — proving chmod is a genuinely independent second barrier, not decoration.
  Test passed as part of the 306-test green suite I ran myself.
- `CLAUDE.md` (repo root) has a real "## Lockbox" section (confirmed, line 163) and `mvp/spec.md`
  has the DON'T at line 228 — both present, not just described in CONTEXT.md.
- `lockbox_POLICY.md` exists, is honest about scope ("does NOT protect against... a determined
  same-uid agent... a tired human"), matching the phase's stated non-adversarial scope.

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `data/unit_registry.py` + `spec/unit_registry.toml` | Per-dataset unit registry | ✓ VERIFIED | Real `futures-um.trades` entry measured against real archive file; unknown-pair rejection mechanical |
| `data/backfill/client.py`, `data/backfill/downloader.py` | Checksum-verified, idempotent backfill | ✓ VERIFIED | `download_and_verify`, `extract_expected_member` (no `extractall()`), real 107-day run recorded in `03-03-SUMMARY.md`; row-conservation sanity check in `ingest_monthly` |
| `data/ingest/trade_side.py` | Side resolution + cross-check | ✓ VERIFIED | Wired at `curated_build.py:299`; real-data spot check confirms `side_method=exact_flag` for all 3,688,572 rows on 09-14 |
| `data/ingest/curated_build.py:materialize_seq` | Deterministic `(etime, seq)` | ✓ VERIFIED | My own 3.7M-row shuffle test, byte-identical parquet output |
| `data/store.py` | Manifest-addressed loader, RP-1/RP-2 | ✓ VERIFIED (RP-2), ⚠️ RP-1 CI leg inert (see above) | `resolve_manifest`/`load_curated` read and traced directly |
| `tools/check_no_manifest_rewrite.py` | Immutability guardrail, wired both callers | ⚠️ WARNING | Genuinely enforced via pre-push locally (I red-proved it); CI leg unconditionally SKIPs (structural, disclosed) |
| `data/dq/checks.py`, `data/dq/report.py` | Six DQ checks + report | ✓ VERIFIED | Real 111-manifest run; checks (2)/(3) read persisted stats; fail-closed absence confirmed on real 07-14 data |
| `data/store.py:_enforce_dq_pause`/`DQPauseError` | Mechanical training pause | ✓ VERIFIED | Real acknowledgements (6 files) match real failed/degraded rows (6 combos) exactly |
| `data/lockbox.py`, `tools/check_lockbox_containment.py` | Quarantine mechanism + AST guardrail | ✓ VERIFIED | chmod 0000 live; AST scan passes; RP-3 both directions in green test suite |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `curated_build.py:build_curated_day` | `trade_side.py:resolve_side` | direct call, line 299 | WIRED | Confirmed by grep + real-data output |
| `curated_build.py` | `store.py:issue_manifest` | direct call | WIRED | 111 real manifests exist, all resolve |
| `store.py:load_curated` | `store.py:_enforce_dq_pause` | direct call | WIRED | Confirmed by code read + passing pytest suite |
| `.pre-commit-config.yaml` (pre-push stage) | `tools/check_no_manifest_rewrite.py --full` | byte-identical command string | WIRED (locally) | Verified `.git/hooks/pre-push` is a real installed hook, ran the command myself |
| `.github/workflows/ci.yml` | `tools/check_no_manifest_rewrite.py --full` | byte-identical command string | WIRED but INERT | Command present and identical, but structurally always SKIPs on a GH Actions runner (no `/Volumes/ProjectsSSD` mount possible) |
| `.pre-commit-config.yaml` + `ci.yml` | `tools/check_lockbox_containment.py` | byte-identical command string | WIRED, LIVE | No lake-mount dependency; genuinely runs for real in both callers |
| `data/lockbox.py:open_lockbox` | `tracking/mlflow_utils.py:start_tracked_run` | sole MLflow entry point | WIRED | Confirmed by code read; RP-3's token test passed |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|---------------------|--------|
| `lake/curated/.../trade/date=2026-09-14` | `tradeSide_raw`/`_corrected`/`side_method` | `resolve_side` over real archive/capture rows | Yes — 3,688,572 real rows, non-trivial distribution (`mean=-0.034`, both signs present) | ✓ FLOWING |
| `lake/dq/date=*/report.parquet` | `dq_status` per check | `data.dq.report` over real manifests + real gap ledger | Yes — real failed/degraded findings tied to actual documented battery-sleep outages | ✓ FLOWING |
| `lake_registry/dq_acknowledgements/*.json` | acknowledgement reasons | git-committed, human-written | Yes — 6 real files citing real root causes, matching the 6 real failed/degraded combos exactly | ✓ FLOWING |
| `lake/lockbox/` | (no data — deliberately empty) | N/A (Phase 3 scope: mechanism only, not populated) | N/A by design | Consistent with CONTEXT.md's stated scope |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full test suite | `./.venv/bin/python3 -m pytest tests -q` | `306 passed in 15.97s` | ✓ PASS |
| ms→ns single-site guardrail | `python -m tools.check_ms_to_ns_site` | `PASS: exactly one ms-to-ns site` | ✓ PASS |
| Lockbox containment guardrail | `python -m tools.check_lockbox_containment` | `scanned 47 files`, 0 violations | ✓ PASS |
| Manifest immutability guardrail, full mode | `python -m tools.check_no_manifest_rewrite --full` | `checked 111 manifest(s)`, 0 findings | ✓ PASS |
| RP-1 red-proof (append byte → FAIL; restore → PASS) | manual, both fast and full modes | FAIL then PASS, both directions, on real 498 MB partition | ✓ PASS |
| `(etime, seq)` determinism at scale | shuffle 3.7M real rows, compare sha256 | identical | ✓ PASS |
| ruff lint | `ruff check .` | `All checks passed!` | ✓ PASS |
| Capture daemon live, schema v2 populating | `ps -p 75796`; read latest real trade partition | Daemon alive (elapsed 02:13:41); `exec_type` present, `schema_version=2`, real `MARKET`/`NA` values | ✓ PASS |

### Probe Execution

No `scripts/*/tests/probe-*.sh` convention is used by this phase; its "probes" are the RP-1…RP-4
red-proofs, covered above (RP-1 re-run by me; RP-2/RP-3/RP-4 previously red-proved by the
orchestrator and cross-checked here by reading the relevant code/tests directly).

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|--------------|--------|----------|
| DATA-02 | 01, 03, 06 | Trades backfilled with unit registry, format heterogeneity | ✓ SATISFIED | `data/backfill/client.py`, `data/backfill/downloader.py:run_backfill`, `data/unit_registry.py:get_unit_entry`; real 107-day, 376.2M-row backfill |
| DATA-03 | 02 | Legacy trade-side classification, raw/corrected stored | ✓ SATISFIED | `data/ingest/trade_side.py:resolve_side`/`nearest_quote_side`/`cross_check_agreement`, wired in `curated_build.py:299` |
| DATA-05 | 02 | `(etime, seq)` deterministic arrival order | ✓ SATISFIED | `data/ingest/curated_build.py:materialize_seq`; my own 3.7M-row order-independence proof |
| DATA-06 | 02 | Manifest-addressed, immutable, versioned-schema lake | ✓ SATISFIED, ⚠️ CI-enforcement caveat | `data/store.py:issue_manifest`/`resolve_manifest`/`load_curated`; `tools/check_no_manifest_rewrite.py` (local pre-push leg real, CI leg structurally inert — see WARNING above) |
| DATA-07 | 04 | Daily DQ report, mechanical training pause | ✓ SATISFIED | `data/dq/checks.py` (six checks), `data/dq/report.py`, `data/store.py:_enforce_dq_pause`/`DQPauseError`; real acknowledgements match real findings |
| DATA-08 | 05 | Lockbox mechanically enforced, one-look unlock | ✓ SATISFIED | `data/lockbox.py:open_lockbox`/`issue_token`, `tools/check_lockbox_containment.py`; chmod 0000 live |

No orphaned requirements — all six plans' `requirements:` frontmatter fields together cover
exactly DATA-02/03/05/06/07/08, matching `.planning/REQUIREMENTS.md`'s phase-3 mapping.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `mvp/tools/check_no_manifest_rewrite.py` | `main()` | CI leg unconditionally `SKIP`s (no lake mount possible on GH Actions) | WARNING (disclosed, T-03-09) | The CI leg of the phase's primary immutability guardrail can never perform a real check; only the local pre-push hook and per-read `resolve_manifest` verification are load-bearing |
| `mvp/data/dq/checks.py` | `check_l1_sparsity` | `etimes.len() < 2` returns `"ok"` rather than `"n/a"`/`"failed"` | WARNING (latent, not triggered by current data) | A 0/1-row L1 partition would silently pass sparsity; no real day currently has this shape |
| `mvp/data/dq/checks.py` | module docstring / `resync_windows_for_date` | `rtime`-as-`etime` approximation for outage boundaries | INFO (disclosed deviation from "rtime joins nothing") | Justified (an outage has no etime by definition); Phase 4 must treat the join as approximate |

No `TBD`/`FIXME`/`XXX`/`TODO`/`HACK`/`PLACEHOLDER` markers found in any file created/modified by
this phase (`data/backfill/`, `data/ingest/`, `data/dq/`, `data/store.py`, `data/lockbox.py`,
`tools/check_no_manifest_rewrite.py`, `tools/check_lockbox_containment.py`,
`tools/reframe_raw_archive.py`).

### Human Verification Required

1. **T-03-09 disposition confirmation** — see frontmatter `human_verification` entry above. Either
   accept the current design (CI's manifest-rewrite leg cannot ever perform a real check, real
   enforcement is per-read hash verification + local pre-push hook) explicitly, or approve the
   minimal CI-native self-consistency check as a follow-up.

### Gaps Summary

No must-have truth failed outright. All six ROADMAP success criteria are genuinely built and
substantively wired, verified against real data (107 trade days / 376.2M rows, 4 bookTicker days
/ 104.9M rows, 111 manifests) rather than SUMMARY claims — I re-ran the RP-1 immutability
red-proof myself against a real 498 MB committed partition (both directions), and independently
proved `(etime, seq)` determinism at 3.7M-row scale by shuffling real data and comparing sha256
hashes.

The one substantive finding is that the phase's own stated bar — "every guardrail in BOTH
pre-commit AND CI, byte-identical" — is technically met (the command strings ARE byte-identical)
but the CI leg of `check_no_manifest_rewrite --full` can never do real work on a GitHub Actions
runner, by physical construction, not by omission. This is disclosed in the code and in
`03-02-SUMMARY.md`, and the load-bearing guarantee (per-read manifest-hash verification) is
independent of it and verified working — so this is a WARNING requiring a human accept/fix
decision, not a BLOCKER. Two smaller, currently-latent DQ edge cases (`check_l1_sparsity`'s
`<2`-row "ok", and the `rtime`-as-`etime` approximation) are noted for awareness; neither is
triggered by real data today.

---

_Verified: 2026-09-16T15:30:00Z_
_Verifier: Claude (gsd-verifier)_
