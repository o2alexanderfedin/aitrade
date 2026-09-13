---
phase: 01-capture-daemon-repo-foundation
plan: 01
subsystem: infra
tags: [uv, polars, numba, websockets, orjson, binance, parquet, walking-skeleton]

# Dependency graph
requires: []
provides:
  - "mvp/ repo skeleton pinned to Python 3.13 + polars/numpy/numba/websockets/orjson/zstandard exact versions, uv.lock honoring the numba 0.65.1/numpy 2.4.6/llvmlite 0.47.0 pin chain"
  - "mvp/data/schema.py: SCHEMA_VERSION, BOOKTICKER_SCHEMA, TRADE_SCHEMA, assert_non_null_etime() — the canonical row-dtype contract"
  - "mvp/data/capture/config.py: validate_data_root() data_root guard (CloudStorage/OneDrive/existence/writability/free-space)"
  - "mvp/data/capture/streams.py: combined_public_stream_url(), assert_secure_url()"
  - "mvp/data/capture/parse.py: decode_frame/parse_bookticker/parse_trade/parse_combined_frame, single ms-to-ns conversion site"
  - "mvp/data/capture/seq.py: SeqAssigner (post-merge monotonic seq per (symbol, stream))"
  - "mvp/scripts/verify_live_connection.py: one-shot live proof + --redundancy-check diagnostic, both run successfully against the real exchange"
  - "SKELETON.md: locked architectural decisions, now marked proven not aspirational"
affects: [01-02, 01-03, 01-04]

# Tech tracking
tech-stack:
  added: [uv 0.11.6, polars 1.41.2, numpy 2.4.6, numba 0.65.1, llvmlite 0.47.0, websockets 16.1.1, orjson 3.11.9, zstandard 0.25.0, ruff 0.15.22, pytest 9.1.1, hypothesis 6.168.0]
  patterns: ["single ms-to-ns conversion site (_ms_to_ns in parse.py)", "seq assigned post-merge by single writer, never in socket callback", "atomic Parquet write via .tmp suffix + Path.replace", "data_root guard runs before any socket opens"]

key-files:
  created:
    - mvp/pyproject.toml
    - mvp/uv.lock
    - mvp/.gitignore
    - mvp/.python-version
    - mvp/data/schema.py
    - mvp/data/capture/config.py
    - mvp/data/capture/streams.py
    - mvp/data/capture/parse.py
    - mvp/data/capture/seq.py
    - mvp/scripts/verify_live_connection.py
    - mvp/tests/conftest.py
    - mvp/tests/capture/test_config_guard.py
    - mvp/tests/capture/test_parse.py
    - mvp/tests/fixtures/payloads.py
  modified:
    - .planning/phases/01-capture-daemon-repo-foundation/SKELETON.md

key-decisions:
  - "argparse --data-root made optional (default=None) instead of required=True, because the --redundancy-check acceptance command omits it; validate_data_root() still enforces the required/no-default rule at call time via `args.data_root or ''`"
  - "Real captured live envelopes (not placeholders) now live in tests/fixtures/payloads.py, per Task 4's explicit instruction"

patterns-established:
  - "TDD RED/GREEN cycle: test commit first (confirmed failing with ModuleNotFoundError), then implementation commit"
  - "grep-verifiable single-conversion-site contract: `grep -v '^\\s*#' parse.py | grep -c \"1_000_000\"` == 1 — kept docstrings free of the literal"

requirements-completed: [DATA-01, DATA-04]

duration: 7min
completed: 2026-09-12
---

# Phase 1 Plan 01: Repo Scaffold + Live Walking-Skeleton Proof Summary

**Live websocket connection to the real Binance USD-M futures exchange, parsed into canonical int64-ns-etime rows and durably round-tripped through one Parquet file — proven against real BTCUSDT traffic, not a mock, with the redundancy-dedup assumption (A1) re-verified at Jaccard 1.000000.**

## Performance

- **Duration:** ~7 min (tool-call wall time; excludes review/read time)
- **Started:** 2026-09-12T05:38:49Z
- **Completed:** 2026-09-12T05:45:33Z
- **Tasks:** 4/4 completed
- **Files modified:** 18 (14 created under `mvp/`, 1 modified under `mvp/`, 1 modified under `.planning/`, plus `mvp/scripts/__init__.py` and `mvp/tests/fixtures/__init__.py`, `mvp/tests/capture/__init__.py`)

## Accomplishments

- Stood up the `mvp/` uv-managed repo with the exact CLAUDE.md-mandated pin chain: numba 0.65.1 / numpy 2.4.6 / llvmlite 0.47.0, verified directly in `uv.lock`.
- Locked the canonical `BOOKTICKER_SCHEMA`/`TRADE_SCHEMA` dtype contract and the single ms-to-ns conversion site, both grep-verifiable per the plan's acceptance criteria.
- Ran `verify_live_connection.py` against the real Binance exchange twice: once in normal mode (one Parquet file written and read back) and once in `--redundancy-check` mode (reproducing Assumption A1's Jaccard=1.000000 result from `evidence/probe_a1.py`).
- Overwrote the parser test fixtures with byte-real captured envelopes; the existing unit tests continued to pass unchanged against real data.

## Task Commits

Each task was committed atomically:

1. **Task 1: Repo/environment scaffold + canonical schema contract** - `5292319` (feat)
2. **Task 2: data_root guard + URL builder + seq assigner** - `60f84f1` (test, RED) → `35120e4` (feat, GREEN)
3. **Task 3: Frame parser (single ms-to-ns conversion site) + fixture payloads** - `328d802` (test, RED) → `4010b11` (feat, GREEN)
4. **Task 4: Live walking-skeleton proof against the real exchange** - `a44aa6d` (feat)

**Plan metadata:** committed separately below (docs: complete plan)

## Files Created/Modified

- `mvp/pyproject.toml` — project metadata, exact dependency pins, `[tool.pytest.ini_options]`, ruff banned-api pandas rule
- `mvp/uv.lock` — resolved lockfile; numba 0.65.1 / numpy 2.4.6 / llvmlite 0.47.0 confirmed
- `mvp/.gitignore` — `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.ruff_cache/`, `.hypothesis/`
- `mvp/.python-version` — uv-generated, committed (3.13)
- `mvp/data/schema.py` — `SCHEMA_VERSION`, `BOOKTICKER_SCHEMA`, `TRADE_SCHEMA`, `assert_non_null_etime()`
- `mvp/data/capture/config.py` — `DataRootError`, `validate_data_root()`
- `mvp/data/capture/streams.py` — `combined_public_stream_url()`, `assert_secure_url()`
- `mvp/data/capture/parse.py` — `FrameParseError`, `decode_frame`, `parse_bookticker`, `parse_trade`, `parse_combined_frame`, `_ms_to_ns` (single conversion site)
- `mvp/data/capture/seq.py` — `SeqAssigner`
- `mvp/scripts/verify_live_connection.py` — normal-mode live proof + `--redundancy-check` diagnostic
- `mvp/tests/conftest.py`, `mvp/tests/capture/{__init__.py,test_config_guard.py,test_parse.py}`, `mvp/tests/fixtures/{__init__.py,payloads.py}` — 12 passing unit tests
- `.planning/phases/01-capture-daemon-repo-foundation/SKELETON.md` — checklist flipped to `[x]`, "Capability Proven End-to-End" made factual

## Decisions Made

- **argparse `--data-root` is optional, not `required=True`.** The plan's own Task 4 acceptance criteria run `--redundancy-check --duration-seconds 45` with no `--data-root` flag at all — `required=True` would make that invocation fail with a usage error before any code runs. Resolved by defaulting to `None` and passing `args.data_root or ""` into `validate_data_root()`, which raises the same "required, no default" `DataRootError` for both `None` and `""`. The required/no-default semantics from `01-CONTEXT.md` are preserved; only the enforcement point moved from argparse to the validator. Documented inline in the script and as a deviation in the Task 4 commit.
- **`tests/__init__.py` omitted.** Per the plan's file list, only `tests/capture/__init__.py` and `tests/fixtures/__init__.py` are regular packages; `tests/` itself relies on Python 3's implicit namespace-package resolution plus `pythonpath = ["."]`. Confirmed working: `from tests.fixtures.payloads import ...` resolves correctly in `test_parse.py` and `pytest --collect-only tests` collects all 12 tests cleanly.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug, forced by the plan's own acceptance criteria] `--data-root` cannot be `argparse.required=True`**
- **Found during:** Task 4
- **Issue:** The plan's action text implies `--data-root` is "required" per 01-CONTEXT.md, but its own acceptance criteria invoke the script's `--redundancy-check` mode without `--data-root` at all.
- **Fix:** Made `--data-root` default to `None` in argparse; normal mode calls `validate_data_root(args.data_root or "", ...)`, which raises the same required-value `DataRootError` for a missing value. `--redundancy-check` mode never calls `validate_data_root` at all (by design — it's a read-only network diagnostic, matching the plan's own text: "skip `validate_data_root` entirely ... this mode reads only, writes nothing to disk").
- **Files modified:** `mvp/scripts/verify_live_connection.py`
- **Verification:** Ran both acceptance commands live — `--redundancy-check --duration-seconds 45` (no `--data-root`) succeeded with Jaccard=1.0; `--data-root /Users/x/Library/CloudStorage/OneDrive-Personal/fake` (no duration/redundancy flags) correctly exited 1 with "CloudStorage" in stderr.
- **Commit:** `a44aa6d`

**2. [Rule 1 - Bug] `requirements.mark-complete` marked DATA-01 fully Complete; corrected to In Progress**
- **Found during:** State-update step, after running `gsd-sdk query requirements.mark-complete DATA-01 DATA-04`
- **Issue:** The plan's frontmatter lists `requirements: [DATA-01, DATA-04]`, so the mechanical state-update step marked both fully "Complete" in `REQUIREMENTS.md`. DATA-04 (int64-ns timestamps, etime-only clock) is genuinely fully satisfied by this plan. DATA-01 is not: its full text is "Redundant capture daemon records ... to Parquet with a gap ledger, running from Phase 1 onward" — redundancy, the gap ledger, and continuous operation are explicitly Plans 02-04's job per `01-VALIDATION.md`'s per-task map and `SKELETON.md`'s "Out of Scope" section. Marking it Complete after only the one-shot walking-skeleton proof would misrepresent phase status.
- **Fix:** Reverted `DATA-01`'s checkbox to `[ ]` and its traceability-table status to "In Progress (walking skeleton proven live in Plan 01; redundancy/gap-ledger/continuous-daemon in Plans 02-04)". Left `DATA-04` marked Complete.
- **Files modified:** `.planning/REQUIREMENTS.md`
- **Verification:** `grep -n "DATA-01" .planning/REQUIREMENTS.md` shows the corrected checkbox and table row.
- **Commit:** part of this plan's final `docs(01-01)` metadata commit.

**3. [Rule 1 - Bug] Docstring literal `1_000_000` in `parse.py` broke the single-conversion-site grep**
- **Found during:** Task 3, GREEN verification
- **Issue:** `grep -v '^\s*#' data/capture/parse.py | grep -c "1_000_000"` returned 2, not 1 — the module docstring quoted 01-CONTEXT.md verbatim including the literal `1_000_000`, and `grep -v '^\s*#'` only strips `#`-comment lines, not docstring lines.
- **Fix:** Reworded the docstring to say "multiplied by one million" instead of quoting the literal, leaving `_ms_to_ns`'s `ms * 1_000_000` as the sole occurrence.
- **Files modified:** `mvp/data/capture/parse.py`
- **Verification:** `grep -v '^\s*#' data/capture/parse.py | grep -c "1_000_000"` now returns exactly `1`; `test_parse.py` still passes.
- **Commit:** `4010b11`

## Live Verification Output (Task 4)

### Normal-mode live proof (2026-09-12, real Binance exchange)

```
$ uv run --directory mvp python -m scripts.verify_live_connection \
    --data-root /Volumes/ProjectsSSD/aihedgefund/capture --symbol BTCUSDT \
    --duration-seconds 30 --dump-fixtures <scratchpad>/captured_frames.json

OK: bookTicker=19 trade=1 path=/Volumes/ProjectsSSD/aihedgefund/capture/skeleton_verify/verify_1789191817.parquet
```
Exit code: 0

**Parquet read-back inspection:**
```
height: 20
etime dtype: Int64
etime null_count: 0
etime min/max: 1789191814815000000 1789191815354000000
```
(20 rows = 19 bookTicker + 1 trade; `assert_non_null_etime` and `height >= 2` both passed inside the script before it printed `OK:`.)

**Captured real envelopes** (used to overwrite `tests/fixtures/payloads.py`):
```json
{"bookticker": {"stream": "btcusdt@bookTicker", "data": {"e": "bookTicker", "u": 11538015451847, "s": "BTCUSDT", "ps": "BTCUSDT", "b": "77199.90", "B": "5.832", "a": "77200.00", "A": "12.001", "T": 1789191814815, "E": 1789191814815, "st": 1}}, "trade": {"stream": "btcusdt@trade", "data": {"e": "trade", "E": 1789191815354, "T": 1789191815354, "s": "BTCUSDT", "t": 8072551060, "p": "77199.90", "q": "0.005", "X": "MARKET", "m": true, "st": 1}}}
```

### Redundancy-check re-proof of Assumption A1 (2026-09-12, real Binance exchange, 45s)

```
$ uv run --directory mvp python -m scripts.verify_live_connection --redundancy-check --duration-seconds 45

bookTicker: A_total=1220 B_total=1079 overlap_A=1079 overlap_B=1079 identical=1079 only_A=0 only_B=0 jaccard=1.000000
trade: A_total=75 B_total=66 overlap_A=66 overlap_B=66 identical=66 only_A=0 only_B=0 jaccard=1.000000
```
Exit code: 0

### Negative case — CloudStorage rejection

```
$ uv run --directory mvp python -m scripts.verify_live_connection \
    --data-root /Users/x/Library/CloudStorage/OneDrive-Personal/fake

data_root '/Users/x/Library/CloudStorage/OneDrive-Personal/fake' is under a CloudStorage-synced path — refusing to write capture data there
```
Exit code: 1 (no socket ever opened)

### Full unit suite

```
$ uv run --directory mvp pytest tests -x -q
............
12 passed in 0.18s
```

```
$ uv run --directory mvp ruff check .
All checks passed!
```

## Known Stubs

None. This plan's script is a one-shot verification CLI by design (superseded by the continuous daemon in Plan 02), not a stub of a daemon — `SKELETON.md`'s "Out of Scope" section explicitly defers continuous operation to Plan 02.

## Threat Flags

None. All threat-register items (T-1-01 through T-1-05) from the plan's `<threat_model>` are implemented exactly as specified: `validate_data_root()` (T-1-01), `assert_secure_url()` with no `ssl=False` anywhere (T-1-02), `decode_frame`/`parse_combined_frame` try/except plus the script's `TimeoutError`/`ConnectionClosed` handling (T-1-03), atomic `.tmp` + `Path.replace()` write (T-1-04). No new network endpoints, auth paths, or schema surfaces were introduced beyond what the threat model already covers.

## Self-Check: PASSED

Verified file existence:
```
FOUND: mvp/pyproject.toml
FOUND: mvp/uv.lock
FOUND: mvp/.gitignore
FOUND: mvp/data/schema.py
FOUND: mvp/data/capture/config.py
FOUND: mvp/data/capture/streams.py
FOUND: mvp/data/capture/parse.py
FOUND: mvp/data/capture/seq.py
FOUND: mvp/scripts/verify_live_connection.py
FOUND: mvp/tests/capture/test_config_guard.py
FOUND: mvp/tests/capture/test_parse.py
FOUND: mvp/tests/fixtures/payloads.py
```

Verified commits exist in `git log --oneline --all`:
```
FOUND: 5292319 feat(01-01): scaffold mvp repo and lock canonical capture schema
FOUND: 60f84f1 test(01-01): add failing tests for data_root guard and seq assigner
FOUND: 35120e4 feat(01-01): implement data_root guard, stream URL builder, seq assigner
FOUND: 328d802 test(01-01): add failing tests for frame parser and sample payloads
FOUND: 4010b11 feat(01-01): implement frame parser with single ms-to-ns conversion site
FOUND: a44aa6d feat(01-01): prove capture stack live against the real Binance exchange
```

All claims verified. No missing items.
