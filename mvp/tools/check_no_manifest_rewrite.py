"""CI-callable guardrail: every committed manifest's partition files must
still be exactly the bytes the manifest names (T-03-03, RP-1, RP-2).

Two speeds, mirroring `check_latest_ban.py`'s `PKG_ROOT`-anchoring and
`main()`/exit-code shape:

- `verify_manifest_fast` (default, per-commit): compares each partition's
  recorded `(size_bytes, mtime_ns)` (captured at manifest-issuance time by
  `data.store.issue_manifest`) against the on-disk file's current `stat()`,
  never reading file contents -- cheap enough for a per-commit hook once
  real data exists (several GB across committed manifests).
- `verify_manifest` (`--full`, pre-push + CI): recomputes each partition's
  sha256 and compares against the manifest's stored value -- the real
  guarantee. A per-commit hook that sha256s several GB invites
  `--no-verify`, which would destroy the guardrail's whole value; the fast
  form is the per-commit tripwire, and the sha256 guarantee still runs
  before anything leaves the machine via the `pre-push`-staged hook.

Both `verify_manifest`/`verify_manifest_fast` are pure functions (`dict`,
`Path` -> `list[str]` of the partition paths that failed) so
`tests/store/test_manifest_rewrite_guard.py` can call them directly against
a `tmp_path` fixture -- zero dependency on the real mounted
`/Volumes/ProjectsSSD` lake, which is what makes this guardrail's own
correctness provable in CI, where the real lake is never mounted.

`main()`'s real-lake scan does a best-effort pass over every
`LAKE_REGISTRY_ROOT / "manifests" / **/*.json` EXCLUDING `by-date/` index
files (which are `{"manifest_id": ...}` pointers, not manifests, and have
no `partitions` key) against the REAL `lake_root()`. Deliberately does NOT
call `data.lake_paths.lake_root()` here -- that function `mkdir(parents=
True)`s the target and runs `validate_data_root`'s write-validation, which
would raise on a machine where `/Volumes/ProjectsSSD` doesn't exist at all
(e.g. a GitHub Actions runner) before this module ever reaches its own
SKIP branch. Uses a bare `Path(DEFAULT_LAKE_ROOT).exists()` read-only check
instead: if the DEFAULT root does not exist and no explicit `--lake-root`
was passed, prints `SKIP (lake root not mounted: ...) -- N manifest(s)
unverified` and exits 0 (T-03-09, an honest, disposition=accept blind spot
for machines without the SSD mounted). If the root DOES exist but a
specific manifest's partition is missing or mismatched, that is a real
finding and exits 1 naming the path -- never silently skipped just because
SOME manifests are unverifiable.

CI-NATIVE FIXTURE LEG (03-VERIFICATION.md gap-closure, finding 1): the
above SKIP made this module's CI leg structurally unable to ever exercise
`verify_manifest`'s real scan code on a GitHub Actions runner, forever, by
construction -- a guardrail that can never fire in the one place that runs
on every push. `--lake-root`/`--registry-root` (both optional, default to
the real roots above) let a caller point the REAL scan path at a small,
committed fixture lake instead: `mvp/tests/fixtures/lake` +
`mvp/tests/fixtures/lake_registry` (see that directory's own generator
script) -- a few KB, git-committed, present on every runner. Both CI
callers now run `--full --lake-root tests/fixtures/lake --registry-root
tests/fixtures/lake_registry` as an ADDITIONAL step alongside the existing
real-lake `--full` step (which keeps SKIPping on GitHub Actions, honestly,
as before) -- this is what makes `verify_manifest`'s actual byte-comparison
code path execute on every CI run rather than short-circuiting. An
EXPLICITLY passed `--lake-root` that does not exist is a hard FAIL, never a
SKIP: only the UNSET default may SKIP, so a typo'd CI path cannot silently
resurrect the exact inert-guardrail failure this fixture leg exists to fix.
Git does not preserve `mtime`, so the fixture lake only ever green-lights
`--full` (sha256) -- never wire `verify_manifest_fast` against it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from data.lake_paths import DEFAULT_LAKE_ROOT, LAKE_REGISTRY_ROOT

PKG_ROOT = Path(__file__).resolve().parents[1]

BY_DATE_MARKER = "/by-date/"


def verify_manifest(manifest: dict, lake_root: Path) -> list[str]:
    """Recompute each `partitions[]` entry's on-disk sha256 and compare
    against the manifest's stored value. Returns the list of partition
    paths whose recomputed hash does not match (missing file counts as a
    mismatch); empty list = all good.

    Stored-hash-vs-on-disk-hash, never rebuild-vs-compare (03-RESEARCH.md
    Pattern 3).
    """
    bad: list[str] = []
    for part in manifest["partitions"]:
        on_disk_path = Path(lake_root) / part["path"]
        if not on_disk_path.exists():
            bad.append(part["path"])
            continue
        on_disk_hash = hashlib.sha256(on_disk_path.read_bytes()).hexdigest()
        if on_disk_hash != part["sha256"]:
            bad.append(part["path"])
    return bad


def verify_manifest_fast(manifest: dict, lake_root: Path) -> list[str]:
    """Compare each `partitions[]` entry's recorded `(size_bytes, mtime_ns)`
    against the on-disk file's current `stat()`, never reading file
    contents. Returns the list of mismatching/missing partition paths.
    """
    bad: list[str] = []
    for part in manifest["partitions"]:
        on_disk_path = Path(lake_root) / part["path"]
        if not on_disk_path.exists():
            bad.append(part["path"])
            continue
        st = on_disk_path.stat()
        if st.st_size != part["size_bytes"] or st.st_mtime_ns != part["mtime_ns"]:
            bad.append(part["path"])
    return bad


def _iter_manifest_files(registry_root: Path) -> list[Path]:
    manifests_dir = registry_root / "manifests"
    if not manifests_dir.exists():
        return []
    return [
        p
        for p in manifests_dir.glob("**/*.json")
        # by-date/ index files are {"manifest_id": ...} pointers, not
        # manifests -- they have no "partitions" key.
        if BY_DATE_MARKER not in f"/{p.relative_to(manifests_dir)}"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full",
        action="store_true",
        help="run the real sha256 check (verify_manifest) instead of the "
        "cheap mtime+size check (verify_manifest_fast)",
    )
    parser.add_argument(
        "--lake-root",
        default=None,
        help="override the physical lake root scanned for partition files "
        "(e.g. a small committed CI fixture lake). Unlike the unset "
        "default, an EXPLICITLY passed --lake-root that does not exist is "
        "a hard FAIL, never a SKIP -- a typo'd CI path must not silently "
        "recreate T-03-09's inert guardrail.",
    )
    parser.add_argument(
        "--registry-root",
        default=None,
        help="override LAKE_REGISTRY_ROOT (the committed manifest JSON "
        "tree scanned for manifest files), e.g. a fixture registry paired "
        "with --lake-root",
    )
    args = parser.parse_args(argv)

    registry_root_path = (
        Path(args.registry_root) if args.registry_root else LAKE_REGISTRY_ROOT
    )
    # 03-REVIEW.md WR-07: a typo'd or empty registry root used to print
    # "checked 0 manifest(s)" and exit 0 -- the fixture leg silently inert.
    # Every registry this tool is pointed at is known to be non-empty, so a
    # missing root or zero manifests is a FAIL, never a pass or a SKIP.
    if not registry_root_path.exists():
        print(f"FAIL: registry root {registry_root_path} does not exist")
        return 1
    manifest_files = _iter_manifest_files(registry_root_path)
    if not manifest_files:
        print(
            f"FAIL: found 0 manifest(s) under {registry_root_path / 'manifests'} "
            "-- a scan that checked nothing must not pass"
        )
        return 1

    if args.lake_root is not None:
        lake_root_path = Path(args.lake_root)
        if not lake_root_path.exists():
            print(f"FAIL: explicit --lake-root {args.lake_root} does not exist")
            return 1
    else:
        lake_root_path = Path(DEFAULT_LAKE_ROOT)
        if not lake_root_path.exists():
            print(
                f"SKIP (lake root not mounted: {DEFAULT_LAKE_ROOT}) -- "
                f"{len(manifest_files)} manifest(s) unverified"
            )
            return 0

    check = verify_manifest if args.full else verify_manifest_fast
    mode = "full (sha256)" if args.full else "fast (mtime+size)"

    all_bad: list[tuple[str, str]] = []
    for manifest_file in manifest_files:
        manifest = json.loads(manifest_file.read_text())
        bad_paths = check(manifest, lake_root_path)
        try:
            manifest_label = str(manifest_file.relative_to(PKG_ROOT))
        except ValueError:
            # Not under PKG_ROOT -- e.g. a test-injected registry_root
            # outside the repo. Print the absolute path rather than raising.
            manifest_label = str(manifest_file)
        for bad_path in bad_paths:
            all_bad.append((manifest_label, bad_path))

    print(f"checked {len(manifest_files)} manifest(s), mode={mode}")

    if all_bad:
        print("FAIL: partition(s) diverged from their manifest:")
        for manifest_rel, bad_path in all_bad:
            print(f"  {manifest_rel} -> {bad_path}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
