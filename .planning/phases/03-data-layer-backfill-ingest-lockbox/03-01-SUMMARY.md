---
phase: 03-data-layer-backfill-ingest-lockbox
plan: 01
subsystem: data-lake
tags: [backfill, s3-listing, checksum-verification, unit-registry, polars, write-once, atomic-write]

# Dependency graph
requires:
  - phase: 01-capture-daemon-repo-foundation
    provides: data/capture/config.py:validate_data_root, data/capture/rotation.py's .tmp+rename atomic-write idiom, data/schema.py's TRADE_SCHEMA/SCHEMA_VERSION, data/capture/parse.py's single ms-to-ns site
  - phase: 02-stage-0-living-spec-ci-guardrails-tracking
    provides: spec/catalogue.py's TOML-registry + frozen-dataclass pattern (mirrored for unit_registry.py), the tools.check_* guardrail convention, mvp/spec.md's Conventions section
provides:
  - mvp/spec/unit_registry.toml + data/unit_registry.py:get_unit_entry() -- the archive (market, dataset) unit-convention contract
  - data/lake_paths.py:lake_root()/backfill_staging_root() -- validated physical roots on the SSD; LAKE_REGISTRY_ROOT constant for the git-committed audit trail (not yet populated)
  - data/backfill/client.py -- list_month/list_paginated (S3 XML listing), download_and_verify/verify_file/fetch_checksum (streaming checksum), extract_expected_member (safe zip extraction), BackfillClient (idempotent per-day download)
  - data/ingest/normalize.py -- normalize_archive_trades() (unit-registry-driven CSV -> canonical TRADE_SCHEMA rows) and write_raw_partition() (write-once raw-tier writer)
  - data/capture/parse.py:ms_to_ns() -- renamed from private _ms_to_ns, now the documented public single conversion site
  - data/capture/rotation.py:write_parquet_atomic() -- extracted atomic-write primitive, reused by write_partition_atomic and this plan's raw-tier writer
  - One real write-once raw partition: lake/raw/symbol=BTCUSDT/stream=trade/source=archive/date=2026-09-12/ (791,576 rows)
affects: [03-data-layer-backfill-ingest-lockbox/03-02, 03-data-layer-backfill-ingest-lockbox/03-03, 04-feature-engine]

# Tech tracking
tech-stack:
  added: []  # stdlib only (urllib.request, xml.etree.ElementTree, zipfile, hashlib) -- no new pyproject.toml dependency
  patterns:
    - "Injectable fetch/open_stream callables (default urllib.request) so backfill-client tests never touch the real network"
    - "verify_file(path, digest) split out from download_and_verify so a fresh download and an already-staged file share one hash-compare implementation"
    - "Write-once raw-tier partition: refuse (FileExistsError) rather than overwrite when a date=... directory already has a part-*.parquet file"
    - "Physical lake data stays off-git (SSD only); a separate git-committed LAKE_REGISTRY_ROOT constant is reserved for the small JSON audit trail Plan 02+ will populate"

key-files:
  created:
    - mvp/spec/unit_registry.toml
    - mvp/data/unit_registry.py
    - mvp/data/lake_paths.py
    - mvp/data/backfill/__init__.py
    - mvp/data/backfill/client.py
    - mvp/data/ingest/__init__.py
    - mvp/data/ingest/normalize.py
    - mvp/tests/backfill/__init__.py
    - mvp/tests/backfill/test_unit_registry.py
    - mvp/tests/backfill/test_downloader.py
    - mvp/tests/backfill/test_extract.py
    - mvp/tests/ingest/__init__.py
    - mvp/tests/ingest/test_normalize.py
    - mvp/tests/fixtures/archive_csv.py
  modified:
    - mvp/spec.md (Conventions section: archive unit-registry bullet)
    - mvp/data/capture/parse.py (_ms_to_ns -> public ms_to_ns, unchanged body)
    - mvp/data/capture/rotation.py (write_parquet_atomic extracted from write_partition_atomic's inline pair)
    - mvp/tests/capture/test_rotation_atomicity.py (two new tests exercising write_parquet_atomic directly)

key-decisions:
  - "The probe zip cached at backfill_probe/ had no .CHECKSUM sidecar alongside it (only the .zip) -- fetched the real sidecar live (96 bytes, confirmed digest matches shasum -a 256) rather than treating it as already-cached; the sidecar was saved into the staging root, never into backfill_probe/, so the probe artifact stays untouched."
  - "rtime_ns for the real slice is the staged zip's own st_mtime_ns (preserved via shutil.copy2 from the original probe download), per normalize.py's documented single rule: local staged-file mtime, never an HTTP Last-Modified header, so there is one rule for this field, not two branches that could diverge."
  - "list_month raises ValueError for any (market, dataset) other than the one measured (futures-um, trades) pair, rather than guessing a URL template from the unit registry's declared shape -- matches CONTEXT.md's 'hardcode the one measured template for now' decision."
  - "S3 ListBucketResult XML carries the http://s3.amazonaws.com/doc/2006-03-01/ default namespace on the live bucket (confirmed by direct curl this session, not just RESEARCH.md's assumption) -- client.py's XML parsing is namespace-aware (root.iter(f'{ns}Key')), unlike the un-namespaced re.findall sketch in 03-RESEARCH.md's Code Examples section, which would have silently returned zero keys against the real bucket."

patterns-established:
  - "TOML unit-registry + tomllib + frozen dataclass, mirroring spec/catalogue.py exactly, extended for use outside the features/labels catalogue domain"
  - "Injectable network-call callables for hermetic testing of a real-network client, without a mocking library"

requirements-completed: [DATA-02]

# Metrics
duration: 12min
completed: 2026-09-15
---

# Phase 3 Plan 01: Backfill Client + Unit Registry + Raw-Tier Normalize Summary

**Stood up the unit registry, checksum-verified backfill client, and write-once raw-tier normalizer, then proved the whole chain once against the real 2026-09-12 BTCUSDT archive day (791,576 rows, zero synthetic substitution) through the actual `BackfillClient`/`normalize_archive_trades`/`write_raw_partition` code path.**

## Performance

- **Duration:** ~12 min (commit span 23:26:39 -> 23:32:46 PDT, plus prior research/orientation reading)
- **Started:** 2026-09-15T23:26:39-07:00
- **Completed:** 2026-09-15T23:32:46-07:00
- **Tasks:** 3 (each one commit)
- **Files modified:** 20 (13 new, 4 modified in Task 3's commit, 3 new __init__.py scaffolding files)

## Accomplishments

- `mvp/spec/unit_registry.toml` + `data/unit_registry.py:get_unit_entry()` resolve `(futures-um, trades)` to the measured `header=yes, time_unit=ms, columns=[id,price,qty,quote_qty,time,is_buyer_maker]` convention and raise `UnitRegistryError` on any unrecognized pair or malformed entry — never a guessed default.
- `data/backfill/client.py` lists a real month via a date-narrowed, namespace-aware S3 XML parse; streams a checksum-verified download that deletes the tmp file and raises before any "verified" state on mismatch; extracts a zip's one named member (rejecting traversal/absolute names as defense-in-depth on top of `zipfile`'s own sanitization); and offers an idempotent `BackfillClient` that makes zero network calls on a second run for an already-verified `(symbol, date)`.
- `data/capture/parse.py`'s `_ms_to_ns` is renamed to public `ms_to_ns` with an unchanged body — `tools.check_ms_to_ns_site` still reports exactly one site (now at `parse.py:36`).
- `data/capture/rotation.py`'s inline `.tmp`+`replace` pair is extracted into `write_parquet_atomic()`, reused by both `write_partition_atomic` (capture, unmodified behavior) and this plan's `write_raw_partition` (backfill).
- `data/ingest/normalize.py` converts an archive CSV into `TRADE_SCHEMA`-shaped rows through the unit registry and the single `ms_to_ns` call site, and writes a write-once raw-tier partition that refuses a second write for the same `(symbol, stream, source, date)`.
- The real 2026-09-12 day was run end-to-end through the actual client/normalize/write path (not a shortcut against a synthetic fixture) and produced a partition matching PROBE-RESULTS.md's row count exactly.

## Task Commits

1. **Task 1: Unit registry (contract)** - `e7866da` (feat)
2. **Task 2: Backfill client -- list, download, verify, extract** - `36fe402` (feat)
3. **Task 3: Normalize + raw-tier write (real day slice)** - `f6ab8b6` (feat)

**Plan metadata:** this commit (docs: complete plan)

## Files Created/Modified

- `mvp/spec/unit_registry.toml` - TOML source of truth for `(futures-um, trades)` and `(spot, trades)` (declared-but-unused) conventions
- `mvp/data/unit_registry.py` - `get_unit_entry(market, dataset)` typed accessor, mirrors `spec/catalogue.py`
- `mvp/data/lake_paths.py` - `lake_root()`/`backfill_staging_root()`, both `validate_data_root`-guarded; `LAKE_REGISTRY_ROOT` constant (unpopulated this plan)
- `mvp/data/backfill/client.py` - `list_month`/`list_paginated`, `download_and_verify`/`verify_file`/`fetch_checksum`, `extract_expected_member`, `BackfillClient`
- `mvp/data/ingest/normalize.py` - `normalize_archive_trades()`, `write_raw_partition()`
- `mvp/data/capture/parse.py` - `_ms_to_ns` renamed to public `ms_to_ns`
- `mvp/data/capture/rotation.py` - `write_parquet_atomic()` extracted
- `mvp/tests/capture/test_rotation_atomicity.py` - two tests added exercising `write_parquet_atomic` directly
- `mvp/tests/backfill/test_unit_registry.py`, `test_downloader.py`, `test_extract.py` - 19 tests, no network access
- `mvp/tests/ingest/test_normalize.py` - 4 tests
- `mvp/tests/fixtures/archive_csv.py` - synthetic 3-row archive CSV fixture
- `mvp/spec.md` - Conventions section gains the archive unit-registry bullet

## Decisions Made

See `key-decisions` in frontmatter. Most consequential for later plans: the S3 XML namespace fix (client.py uses namespace-aware `ET` parsing, confirmed against the live bucket, not the un-namespaced research sketch) and the `rtime_ns` single-rule decision (local file mtime always, never an HTTP header) — both are load-bearing for Plan 03's full-range backfill run.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Plan's premise that the probe zip's `.CHECKSUM` was "already cached, no live network call needed" was wrong**
- **Found during:** Task 3 (real-slice run)
- **Issue:** `ls -la /Volumes/ProjectsSSD/aihedgefund/backfill_probe/` showed only `BTCUSDT-trades-2026-09-12.zip` — no `.CHECKSUM` sidecar. 03-01-PLAN.md's Task 3 text explicitly asserted the sidecar was already known-good and needed no network call.
- **Fix:** Fetched the real sidecar live via `curl`/`urllib.request` against `https://data.binance.vision/.../BTCUSDT-trades-2026-09-12.zip.CHECKSUM` (96 bytes, HTTP 200). Confirmed content (`5aaf475f90b199390c2e80c620a7b3fc1cc0309b648d9a96862888f69c92d78c  BTCUSDT-trades-2026-09-12.zip`) exactly matches PROBE-RESULTS.md's quoted prefix/suffix and the local zip's own `shasum -a 256`. Saved the sidecar into the staging root (`backfill/BTCUSDT/2026-09-12/`), never into `backfill_probe/` — the probe artifact directory was not mutated.
- **Files modified:** none (this was a data-staging action, not a code change) — see Measured Numbers below for the transcript.
- **Verification:** `verify_file()` returned the matching digest; the real slice proceeded.
- **Committed in:** n/a (staged data lives on the SSD, outside git per `lake_paths.py`'s design — see `key-decisions`)

**Total deviations:** 1 auto-fixed (Rule 1 — a stale plan assumption about cached state, corrected against measured reality).
**Impact on plan:** None on code; the fix was a live network fetch of a 96-byte file already exercised by Task 2's tested `fetch_checksum`/`verify_file` code path. No architectural change.

## Measured Numbers (real 2026-09-12 slice)

Run via `mvp/.venv/bin/python3` (not `uv run`, per environment rules), calling the actual `data.lake_paths`, `data.backfill.client`, `data.unit_registry`, and `data.ingest.normalize` modules directly against the cached probe zip staged into the real staging root:

| Step | Value |
|---|---|
| Staging root created | `/Volumes/ProjectsSSD/aihedgefund/backfill` (did not exist before this run) |
| Lake root created | `/Volumes/ProjectsSSD/aihedgefund/lake` (did not exist before this run) |
| Staged zip | `backfill/BTCUSDT/2026-09-12/BTCUSDT-trades-2026-09-12.zip`, 5,974,453 B, mtime preserved via `shutil.copy2` = `1789415990149232699` ns (the original probe download time, 2026-09-14 12:59 local) |
| `.CHECKSUM` sidecar (fetched live) | `5aaf475f90b199390c2e80c620a7b3fc1cc0309b648d9a96862888f69c92d78c  BTCUSDT-trades-2026-09-12.zip` |
| `verify_file()` | PASS, digest matches, 0.00s |
| `extract_expected_member()` | `BTCUSDT-trades-2026-09-12.csv`, 42,041,325 B, 0.07s |
| `normalize_archive_trades()` | **791,576 rows** (matches PROBE-RESULTS.md exactly), 0.06s |
| `rtime_ns` used | `1789415990149232699` (staged zip's own mtime, uniform rule per `normalize.py` docstring) |
| `write_raw_partition()` | `lake/raw/symbol=BTCUSDT/stream=trade/source=archive/date=2026-09-12/part-1789540328053818000.parquet`, 0.07s |
| Partition size | 5,254,394 B |
| Partition sha256 | `070f8052a727eebb88574563f841eac4c9dd2487068670dd8a130a1ea7845467` |
| Second `write_raw_partition()` (real red-proof) | `FileExistsError: raw partition .../date=2026-09-12 already has a written part file: .../part-1789540328053818000.parquet` — observed live, not simulated |
| Cleanup | Extracted 42 MB CSV deleted after successful write; zip + sidecar retained in staging (5.7 MB total); lake partition retained (5.0 MB) — no large temp files left behind |

## Verification Transcript

```
$ uv run --locked --directory mvp pytest tests/backfill/test_unit_registry.py -x -q
5 passed in 1.49s

$ uv run --locked --directory mvp pytest tests/backfill/test_downloader.py tests/backfill/test_extract.py -x -q
14 passed in 0.50s

$ uv run --locked --directory mvp pytest tests/ingest/test_normalize.py tests/capture/test_parse.py tests/capture/test_rotation_atomicity.py -x -q
15 passed in 3.03s

$ uv run --directory mvp python -m tools.check_ms_to_ns_site
PASS: exactly one ms-to-ns site at data/capture/parse.py:36
PASS: 8 seconds-to-ns site(s), all allowlisted

$ uv run --locked --directory mvp pytest tests/backfill tests/ingest -x -q
23 passed in 0.55s

$ uv run --locked --directory mvp pytest tests/capture -x -q
82 passed in 7.43s

$ uv run --locked --directory mvp pytest tests -x -q   # full suite
213 passed in 14.24s
```

Every pre-commit hook (ruff check, ruff format --check, uv lock --check, 6 guardrails, pytest) passed on all three task commits — transcripts shown inline at each `git commit` invocation.

## Surprises

- 03-01-PLAN.md's claim that the probe zip's `.CHECKSUM` sidecar was already cached and needed no live network call was incorrect (see Deviations). The rest of the plan's factual claims (row count, checksum value, column order, day-boundary times) all matched measured reality exactly — this was the one place the plan's premise diverged from the actual filesystem state.
- The S3 bucket's `ListBucketResult` XML default-namespaces every element (`http://s3.amazonaws.com/doc/2006-03-01/`), confirmed by direct `curl` against the live bucket this session. 03-RESEARCH.md's Code Examples section used an un-namespaced `re.findall(r"<Key>([^<]+)</Key>", text)` sketch that happens to still work (regex doesn't care about namespaces), but `client.py`'s `xml.etree.ElementTree`-based implementation (matching RESEARCH's own "Don't Hand-Roll" guidance to use the stdlib XML parser, not regex) required namespace-aware `root.iter(f"{ns}Key")` — a bare `root.findall("Key")` would have silently returned zero keys. Worth flagging for Plan 03, which widens this same code path to the full range.

## Issues Encountered

None blocking. The one deviation (missing cached `.CHECKSUM`) was resolved inline with a live fetch already covered by Task 2's tested code path.

## Next Phase Readiness

- Plan 02 (curated build + manifest + loader) can build directly on this plan's raw partition (`lake/raw/symbol=BTCUSDT/stream=trade/source=archive/date=2026-09-12/`) and on `write_parquet_atomic`/`get_unit_entry`/`BackfillClient` without re-deriving any of them.
- `LAKE_REGISTRY_ROOT` (`mvp/data/lake_registry/`) is declared in `lake_paths.py` but not yet populated — Plan 02 is expected to write the first manifest there per this plan's docstring.
- `mvp/spec/unit_registry.toml`'s `spot.trades` row remains declared-but-unused, as designed — no later plan should resolve it against a real file per 03-CONTEXT.md.
- The staged zip + sidecar remain in `/Volumes/ProjectsSSD/aihedgefund/backfill/BTCUSDT/2026-09-12/` for reuse (the `.verified` marker pattern from `BackfillClient.ensure_downloaded` was not exercised on this exact file, since the real-slice run used `verify_file` directly against a `shutil.copy2`-staged file rather than going through `BackfillClient.ensure_downloaded`'s full download path — both share the same tested `verify_file`/checksum logic, but Plan 03's full-range run is the first to exercise `BackfillClient.ensure_downloaded` end-to-end against a *fresh* live download).

## Self-Check: PASSED

All 11 created files verified present on disk; all 3 task commits (`e7866da`, `36fe402`,
`f6ab8b6`) verified present in `git log`; the real raw partition
(`lake/raw/symbol=BTCUSDT/stream=trade/source=archive/date=2026-09-12/part-1789540328053818000.parquet`)
verified present on disk. No missing items.
