"""CI-callable guardrail: the committed manifest registry is APPEND-ONLY
against git history (03-REVIEW.md CR-02; 03-CONTEXT.md: "a
`check_no_manifest_rewrite` CI check asserts that no committed manifest's
partition hashes ever change").

WHY A THIRD MANIFEST CHECK: the other two only compare the working tree with
itself. `check_no_manifest_rewrite` proves a manifest's partition files match
the hashes that manifest CURRENTLY records; `check_manifest_id_integrity`
proves a manifest agrees with its own id and filename. Neither looks at any
earlier commit, so the reviewer's reproduction passed both: rewrite a
partition in place (prices +1 %), recompute its sha256 into the manifest,
recompute `manifest_id`, rename the file, delete the old manifest. The old
id no longer resolves, MLflow runs tagged `data_hash=<old id>` lose their
data, and nothing fails. Immutability has to be anchored to something the
rewriter cannot also rewrite in the same working tree: git history.

RULES (all resolved against real git objects, every subprocess through
`env=scrubbed_git_env()`):

1. The repository must not be a shallow clone. History that is not present
   cannot be checked, so a shallow clone is a FAIL naming the fix
   (`actions/checkout` `fetch-depth: 0`, which `ci.yml` already sets) --
   never a SKIP, which would be exactly the always-green path 03-VERIFICATION
   flagged and 03-07 closed.
2. No manifest's CONTENT may ever have been DESTROYED. A manifest path
   that was DELETED, MODIFIED or TYPE-CHANGED (e.g. replaced by a symlink)
   in the history of `HEAD` is a violation UNLESS that manifest's blob is
   still present at some manifest path in `HEAD`.

   "Manifest" is a path SHAPE, not a location (03-REVIEW-ITER3.md WR-17):
   any `*.json` with a `manifests` directory among its ancestors, outside
   `by-date/` pointer directories, ANYWHERE in the repository. The
   registry's current path is not trusted: moving the registry (`git mv`
   plus a new `LAKE_REGISTRY_ROOT`, or a symlink to a copy) leaves every
   old manifest path recorded as a delete, and a check filtered to today's
   location never looked there.

   Anchoring on CONTENT rather than on paths (03-FOLLOWUPS.md item 5) is
   what tells a legitimate relocation from an attack wearing one. WR-17's
   first fix made ANY move a violation, which is over-strict: a `git mv`
   that carries every manifest body across loses nothing. So the rules read
   the PRE-IMAGE BLOB (`--raw`, never `--name-status`) and ask whether those
   exact bytes still sit at a manifest path. Dropping a manifest in the
   move, editing one on the way, type-changing one, or moving them
   somewhere with no `manifests` component -- all still fail, because in
   each case some body no longer exists as a manifest. Survival is judged
   WITHIN A REALM (`_realm`, 03-REVIEW-FOLLOWUPS.md CR-02): bytes vouch for
   a deleted manifest only if they reappear at a path that is still in the
   same realm AND still looks like a real registry location. Three realms:
   `fixture` (anything under a `tests` component), `production` (a real
   registry anywhere else), and "not a registry at all" -- a manifest-shaped
   path under one of `NON_REGISTRY_COMPONENTS` (`.planning`, `evidence`,
   `docs`, `examples`, `scratch`, `backup`, ...), which neither vouches for
   another realm's deletion nor is protected as a registry.

   That is what separates the two moves that look identical to a diff. A
   `git mv` of the registry to an ordinary location (`mvp/data/registry_v2/`)
   passes with no table edit and no allowlist entry. Dropping a real
   manifest into `.planning/phases/*/evidence/manifests/` -- a directory
   shape this repository's own planning workflow fills, so an entirely
   innocent-looking commit -- and then deleting it from the registry FAILS,
   because the bytes did not survive in the production realm. The fixture
   realm is keyed on `tests` alone, never on the fixture directory's own
   name, so renaming `mvp/tests/fixtures/` is a rename and not an accusation
   (WR-02).

   Two independent views, because a merge commit hides changes from a plain
   `git log` (03-REVIEW-ITER2.md CR-08):
   a. every commit reachable from `HEAD`, each diffed against EVERY parent
      (`--diff-merges=separate`), with NO pathspec on the walk: a pathspec
      turns on history simplification, which drops a merge whose manifest
      tree equals one parent's (`git checkout --theirs manifests` during a
      merge) -- exactly the resolution that deletes the other side's
      manifests. Paths are filtered afterwards, in Python;
   b. a TREE comparison of the manifests directory between a base and
      `HEAD`: the merge-base with `develop` (then `origin/develop`); when
      that IS `HEAD` (running on develop itself, e.g. the post-merge CI
      push), `HEAD`'s FIRST parent -- so a merge commit is compared with the
      mainline it landed on; for a root commit, the empty tree, stated in
      the output (every manifest is then an addition, and rule 5 still
      refuses zero manifests).
   A rename counts as a delete (`--no-renames`). Manifests are write-once:
   a rebuild issues a NEW manifest and new partition files, and the old
   manifest keeps resolving.
3. Every manifest tracked in `HEAD` must still have its bytes somewhere in
   the working tree, at a manifest path, as a regular file (catches the
   uncommitted delete / edit / typechange before it is ever committed; in a
   pre-commit run the working tree is the content being committed). Same
   content anchoring as rule 2, against the index-plus-working-tree state:
   a STAGED relocation passes, a staged relocation that drops or edits a
   manifest does not. An untracked copy does not count -- a move made with
   `cp` and never `git add`ed leaves nothing git can vouch for.
4. No two manifests in the working tree may name the same
   `partitions[].path` with different `sha256` -- the in-place rewrite plus
   a reissued manifest, even when the old manifest is kept. Paths are
   compared by `data.store.partition_path_key` (normpath, NFC, casefold --
   03-REVIEW-ITER2.md WR-13: `curated/./x` is the same file as `curated/x`),
   and a non-canonical or absolute spelling is itself a violation.
5. `HEAD` or the index must hold at least one manifest under the registry:
   a path bug that matched nothing must not read as "nothing was
   rewritten". The index counts too, so a staged relocation is not mistaken
   for an empty registry; both being empty is still a failure.
6. Nothing under `manifests/` may be a symlink or any other non-regular
   file, neither in `HEAD`'s tree (mode 120000 / gitlink) nor in the
   working tree: a symlink makes rule 4 read some other manifest's bytes.
   The same holds for the registry root and every ancestor of it inside
   the repository (WR-17: a committed `lake_registry -> registry_v2`
   symlink used to be followed by `Path.resolve()` before any rule ran).

Only a directory named exactly `by-date` holds mutable pointers (a rebuild
repoints them); it is excluded from rules 2-4 (03-REVIEW-ITER2.md IN-13: a
`by-date-*` prefix match used to exempt real manifests too).

OUT OF REACH (needs branch protection, not this tool): a force-push that
rewrites `develop`/`main` themselves, and a manifest committed and then
amended/rebased away before it is ever merged.

Needs no mounted lake -- manifest JSON is git-committed -- so it runs
identically in pre-commit and on every CI runner.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
from collections import defaultdict
from pathlib import Path

from data.lake_paths import LAKE_REGISTRY_ROOT
from data.store import partition_path_key, partition_path_problem
from tools.git_env import scrubbed_git_env

PKG_ROOT = Path(__file__).resolve().parents[1]

POINTER_DIR_NAME = "by-date"

#: 05-07-PLAN.md Task 2 (D-05-07, 05-RESEARCH.md Q1): generalized from the
#: single `MANIFESTS_DIR_NAME = "manifests"` to a set of THREE sibling,
#: content-addressed registries -- `harness.segments`'s segment manifests
#: and `harness.errata`'s errata lists are written the same way
#: (`sha256(canonicalize_manifest(body))`, atomic tmp-then-rename) but
#: through their own writers, never `data.store.issue_manifest` (which
#: refuses an empty `partitions` list -- a segment/errata manifest names
#: no partition bytes of its own, D-05-09). `_realm`/`NON_REGISTRY_COMPONENTS`
#: (below) need no change -- they already operate generically on path
#: components, regardless of which registry name matched.
#:
#: 07-10-PLAN.md Task 2 (D-07-22) adds a FOURTH: `predictors/`, where
#: `models.frozen.write_frozen_predictor` lands a fitted model's coefficients
#: under `sha256(canonicalize_manifest(body))` -- same content-addressing,
#: same atomic tmp-then-rename, its own writer, and no `partitions` key
#: (nothing in it for rule 4's `seen` map to populate, exactly like
#: `segments/`/`errata/`). It has to be named here for ONE structural
#: reason: it is a TOP-LEVEL sibling of `segments/`/`errata/`. The
#: `BTCUSDT.predictions` dataset (D-07-38) needs no entry at all -- it is a
#: SUBDIRECTORY of `manifests/`, which every recursive glob below already
#: reaches.
#:
#: What the name buys: `_is_manifest_shaped` (rules 2a, 2b and 3) now treats
#: a frozen predictor body as a manifest, so a committed body that is
#: DELETED or REWRITTEN IN PLACE is refused exactly as a committed manifest
#: is. That is the load-bearing half of freezing a winner's coefficients
#: before the validation window is ever looked at: without it the body would
#: be an ordinary JSON file anyone could edit after the fact, and the
#: "immutable in git before it met val" claim would rest on nothing. Rule
#: 5's per-directory vacuity refusal is the other half, and it is why the
#: first body and this line landed in ONE commit.
REGISTRY_DIR_NAMES: frozenset[str] = frozenset(
    {"manifests", "segments", "errata", "predictors"}
)

#: Refs whose merge-base with HEAD is rule 2b's base, in order.
BASE_BRANCH_REFS: tuple[str, ...] = ("develop", "origin/develop")

_CHANGE_WORDS = {"D": "deleted", "M": "modified", "T": "type-changed"}


class GitHistoryUnavailable(RuntimeError):
    """Raised when git history cannot be consulted honestly (not a repo,
    shallow clone, git failure)."""


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        errors="surrogateescape",
        env=scrubbed_git_env(),
    )
    if result.returncode != 0:
        raise GitHistoryUnavailable(
            f"git {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout


def _git_try(args: list[str], cwd: Path) -> str | None:
    """Like `_git`, but `None` instead of raising (for refs that may not exist)."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=scrubbed_git_env(),
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _is_pointer(rel_to_manifests: str) -> bool:
    return POINTER_DIR_NAME in rel_to_manifests.split("/")[:-1]


def _is_manifest_path(repo_rel: str, manifests_rel: str) -> bool:
    """Is `repo_rel` a manifest of the registry at `manifests_rel`?"""
    if not repo_rel.startswith(manifests_rel + "/") or not repo_rel.endswith(".json"):
        return False
    return not _is_pointer(repo_rel[len(manifests_rel) + 1 :])


def _raw_z(output: str) -> list[tuple[str, str, str, str]]:
    """Parse `--raw -z --no-abbrev` output (optionally with `--format=commit
    %H` headers) into `(commit, status, src_blob, path)`.

    `--raw` rather than `--name-status` (03-FOLLOWUPS.md item 5) because the
    PRE-IMAGE BLOB ID is what decides whether a delete destroyed a manifest
    or merely moved it. Each record is
    `:<srcmode> <dstmode> <srcsha> <dstsha> <status>\0<path>\0`.
    """
    records: list[tuple[str, str, str, str]] = []
    commit = "?"
    tokens = output.split("\0")
    index = 0
    while index < len(tokens):
        token = tokens[index].lstrip("\n")
        index += 1
        if token.startswith("commit "):
            commit = token.split()[1][:12]
        elif token.startswith(":") and index < len(tokens):
            fields = token[1:].split()
            if len(fields) >= 5:
                records.append((commit, fields[4][:1], fields[2], tokens[index]))
            index += 1
    return records


#: The path component that marks a manifest as belonging to the TEST FIXTURE
#: registry rather than the real one. Keyed on `tests` ALONE, never on the
#: fixture directory's own name (03-REVIEW-FOLLOWUPS.md WR-02: requiring both
#: `tests` and `fixtures` meant a `git mv mvp/tests/fixtures mvp/tests/data`
#: -- which carries every byte across -- changed the realm and failed the
#: HISTORY rule, so CI stayed red on every later commit, accusing the author
#: of destroying data they never touched).
FIXTURE_COMPONENT = "tests"

#: Path components that mean "this is not a registry". A manifest-shaped file
#: below any of them is an artefact, an attachment or a copy -- never a
#: location a registry legitimately lives at -- so its bytes may not vouch
#: for a manifest deleted from a real registry, and it is not itself
#: protected as one.
#:
#: Chosen deliberately, from the shapes this repository actually grows:
#: `.planning` and `evidence` are the planning workflow's own artefact
#: directories (CR-02's reproduction used
#: `.planning/phases/*/evidence/manifests/`, an entirely innocent-looking
#: commit); `docs`/`doc` and `examples`/`example` hold prose and samples;
#: `scratch`, `tmp`, `temp`, `backup`/`backups` and `sample`/`samples` are
#: the names people reach for when parking a copy. Matched as an exact path
#: COMPONENT, so `by-date-archive` and `source=archive` are untouched, and
#: matched on the REPOSITORY-RELATIVE path, so a checkout that happens to sit
#: under `/tmp/` or `~/Documents/` is scanned like any other.
NON_REGISTRY_COMPONENTS = frozenset(
    {
        ".planning",
        "evidence",
        "docs",
        "doc",
        "examples",
        "example",
        "scratch",
        "tmp",
        "temp",
        "backup",
        "backups",
        "sample",
        "samples",
    }
)


def _realm(repo_rel: str) -> str | None:
    """Which registry world a manifest path belongs to: `"fixture"` for the
    committed test fixture registry, `"production"` for a real registry
    anywhere else, `None` for a path that is not a registry location at all.

    Content anchoring (rule 2, WR-17) asks "do these bytes still exist at a
    manifest path?". Asked repo-wide, the answer is yes when a REAL manifest
    is `git mv`-ed anywhere manifest-shaped -- the bytes survive, the
    registry's count silently drops, and the day it addressed no longer
    resolves. The first fix split the repository into `fixture` (requiring
    BOTH a `tests` and a `fixtures` component) and `production`; CR-02 showed
    that left every other manifest-shaped path in the production pool, so
    `.planning/phases/*/evidence/manifests/` was still a working laundering
    destination, and `mvp/tests/data/lake_registry/` was both an unprotected
    registry and a valid one.

    Surviving now means surviving in the same realm AND at a path that still
    looks like a real registry location. A relocation to an ordinary
    location (`mvp/data/registry_v2/`) needs no table edit and no allowlist
    entry -- that is what rule 2 exists to allow. A relocation into an
    artefact directory does not, which is the point."""
    parts = repo_rel.split("/")
    if FIXTURE_COMPONENT in parts:
        return "fixture"
    if any(part in NON_REGISTRY_COMPONENTS for part in parts):
        return None
    return "production"


def _head_manifest_blobs(toplevel: Path) -> dict[str, set[str]]:
    """Every blob id sitting at a manifest-shaped path in `HEAD`'s tree,
    grouped by `_realm` -- rule 2's "did this content survive?" set. A
    `defaultdict`, never a pre-seeded pair of keys, so a realm added later
    cannot `KeyError` at the first foreign path."""
    blobs: dict[str, set[str]] = defaultdict(set)
    for record in _git(["ls-tree", "-r", "-z", "HEAD"], toplevel).split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        fields = meta.split()
        realm = _realm(path) if _is_manifest_shaped(path) else None
        if len(fields) >= 3 and realm is not None:
            blobs[realm].add(fields[2])
    return blobs


def _worktree_manifest_blobs(toplevel: Path) -> dict[str, set[str]]:
    """The same realm-keyed sets for the WORKING TREE: hash the on-disk bytes of every
    tracked-or-staged manifest-shaped path, through `git hash-object` so the
    repository's own object format is used.

    Untracked copies do not count: a move made by `cp` without `git add`
    leaves nothing git can vouch for, and the delete it pairs with is
    reported. That is the fail-closed direction."""
    listed = [
        path
        for path in _git(["ls-files", "-z"], toplevel).split("\0")
        if path and _is_manifest_shaped(path)
    ]
    present = [path for path in listed if (toplevel / path).is_file()]
    blobs: dict[str, set[str]] = defaultdict(set)
    if not present:
        return blobs
    hashed = _git(["hash-object", "--no-filters", "--", *present], toplevel)
    for path, line in zip(present, hashed.splitlines(), strict=True):
        realm = _realm(path)
        if line.strip() and realm is not None:
            blobs[realm].add(line.strip())
    return blobs


def _is_manifest_shaped(repo_rel: str) -> bool:
    """Is `repo_rel` a manifest of ANY registry, wherever it lives (rule 2,
    WR-17)? A `.json` below a `manifests`/`segments`/`errata` directory
    (05-07-PLAN.md Task 2, D-05-07), not inside a pointer directory below
    it. `dirs.index(matched)` finds the FIRST registry-name component --
    two registry names nested inside each other is not a shape any writer
    in this repository produces."""
    if not repo_rel.endswith(".json"):
        return False
    dirs = repo_rel.split("/")[:-1]
    matched = next((d for d in dirs if d in REGISTRY_DIR_NAMES), None)
    if matched is None:
        return False
    rest = repo_rel.split("/")[dirs.index(matched) + 1 :]
    return not _is_pointer("/".join(rest))


def resolve_base(toplevel: Path) -> tuple[str, str]:
    """Rule 2b's base: `(tree-ish, human description)`. Never `HEAD` itself
    (a self-comparison would pass vacuously)."""
    head = _git(["rev-parse", "HEAD"], toplevel).strip()
    for ref in BASE_BRANCH_REFS:
        merge_base = _git_try(["merge-base", "HEAD", ref], toplevel)
        if merge_base and merge_base != head:
            return merge_base, f"merge-base with {ref} ({merge_base[:12]})"
    parents = _git(["rev-list", "--parents", "-n", "1", "HEAD"], toplevel).split()[1:]
    if parents:
        return parents[0], f"first parent of HEAD ({parents[0][:12]})"
    empty_tree = _git(["hash-object", "-t", "tree", os.devnull], toplevel).strip()
    return empty_tree, "root commit: empty tree (every manifest is an addition)"


def _toplevel_for(registry_root: Path) -> Path:
    probe_dir = Path(registry_root).absolute()
    while not probe_dir.exists():
        probe_dir = probe_dir.parent
    return Path(_git(["rev-parse", "--show-toplevel"], probe_dir).strip()).resolve()


def _non_regular_entries(manifests_dir: Path, manifests_rel: str) -> list[str]:
    """Rule 6, working-tree side: every symlink / non-regular entry under
    `manifests_dir` (never followed)."""
    errors: list[str] = []
    if manifests_dir.is_symlink():
        return [f"{manifests_rel}: the manifests directory itself is a symlink"]
    if not manifests_dir.exists():
        return errors
    for dirpath, dirnames, filenames in os.walk(manifests_dir, followlinks=False):
        for name in sorted([*dirnames, *filenames]):
            entry = Path(dirpath) / name
            mode = entry.lstat().st_mode
            rel = f"{manifests_rel}/{entry.relative_to(manifests_dir).as_posix()}"
            if stat.S_ISLNK(mode):
                errors.append(f"{rel}: is a symlink -- manifests must be regular files")
            elif not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                errors.append(f"{rel}: is not a regular file or directory")
    return errors


def _in_repo_chain(
    registry_abs: Path, toplevel: Path
) -> tuple[Path, list[Path]] | None:
    """`(top, chain)`: `top` is the highest lexical ancestor of `registry_abs`
    that resolves to `toplevel` (robust to `/tmp -> /private/tmp` ABOVE the
    repository), `chain` every path from just below `top` down to
    `registry_abs` itself -- the in-repository components, never resolved.
    `None` if no ancestor is the repository."""
    lineage = [registry_abs, *registry_abs.parents]
    for index in range(len(lineage) - 1, -1, -1):
        if lineage[index].resolve() == toplevel:
            return lineage[index], list(reversed(lineage[:index]))
    return None


def _symlinked_registry_components(registry_abs: Path, toplevel: Path) -> list[str]:
    """Rule 6 for the registry root and its in-repository ancestors: none may
    be a symlink in the working tree (`lstat`, never followed) or a symlink
    blob (mode 120000) in `HEAD` (WR-17)."""
    located = _in_repo_chain(registry_abs, toplevel)
    if located is None:
        return [f"{registry_abs}: registry root is not inside repository {toplevel}"]
    top, chain = located
    errors: list[str] = []
    for component in chain:
        rel = component.relative_to(top).as_posix()
        if component.is_symlink():
            errors.append(
                f"{rel}: registry root or its ancestor is a symlink in the working "
                "tree -- the registry must be a real directory at its own path"
            )
        listing = _git(["ls-tree", "-z", "HEAD", "--", rel], toplevel)
        for record in listing.split("\0"):
            meta, _, path = record.partition("\t")
            if path == rel and meta.split()[:1] == ["120000"]:
                errors.append(
                    f"{rel}: registry root or its ancestor is a symlink in HEAD -- "
                    "the registry must be a real directory at its own path"
                )
    return errors


def check_append_only(registry_root: Path) -> tuple[list[str], int]:
    """Return `(violations, n_manifests_tracked_in_HEAD)` -- the second
    value SUMMED across every registry directory in `REGISTRY_DIR_NAMES`
    that exists on disk (05-07-PLAN.md Task 2: was one directory's count,
    now three); an empty violation list means append-only holds. Raises
    `GitHistoryUnavailable` if history cannot be consulted."""
    errors, per_directory = _check_append_only_detailed(registry_root)
    return errors, sum(per_directory.values())


def _check_append_only_detailed(
    registry_root: Path,
) -> tuple[list[str], dict[str, int]]:
    """The real implementation: `(violations, {dir_name: tracked_or_staged
    count})` -- one entry per `REGISTRY_DIR_NAMES` member that exists on
    disk. `check_append_only` (the stable, 2-tuple public API every
    existing caller/test destructures) is a thin `sum()` wrapper around
    this; `main()` calls this directly for its per-directory legibility
    line, without a second round of git subprocess calls."""
    registry_abs = Path(registry_root).absolute()
    toplevel = _toplevel_for(registry_abs)

    if _git(["rev-parse", "--is-shallow-repository"], toplevel).strip() == "true":
        raise GitHistoryUnavailable(
            "shallow clone: manifest history is incomplete, so append-only "
            "cannot be verified. Fetch full history (actions/checkout "
            "`fetch-depth: 0`, or `git fetch --unshallow`)."
        )

    # Never resolve the registry path (WR-17): a symlinked registry root or
    # ancestor is a rule-6 violation, not a redirect to follow. Every
    # registry directory is addressed by its LEXICAL in-repository path.
    symlinked = _symlinked_registry_components(registry_abs, toplevel)
    errors: list[str] = list(symlinked)
    located = _in_repo_chain(registry_abs, toplevel)
    if located is None:
        return errors, {}
    top, _chain = located

    # Rules 4, 5 and 6, ONCE PER NAME in REGISTRY_DIR_NAMES that exists on
    # disk under `registry_abs` (05-07-PLAN.md Task 2, 05-PATTERNS.md: a
    # per-directory loop, not a constant swap). A name whose directory does
    # not exist yet is simply SKIPPED -- not an error: not every registry
    # realm is populated at every point in history, and this is exactly
    # what lets every existing `manifests/`-only registry (every scratch
    # `tmp_path` fixture in this file's own test suite, and every commit
    # before this plan's own) keep passing unchanged. Rule 5's vacuity
    # check is therefore PER-DIRECTORY and fires only for a name whose
    # directory PHYSICALLY EXISTS (bytes are sitting there, staged or not)
    # but tracks zero manifests in both HEAD and the index -- exactly the
    # window the guardrail-extension commit closes by landing the first
    # `segments/`/`errata/` manifest in the SAME commit (05-RESEARCH.md
    # Q1's vacuity trap).
    per_directory: dict[str, int] = {}
    seen: dict[str, tuple[str, str]] = {}
    for dir_name in sorted(REGISTRY_DIR_NAMES):
        dir_abs = registry_abs / dir_name
        if not dir_abs.exists():
            continue
        dir_rel = dir_abs.relative_to(top).as_posix()

        # Rule 6 (HEAD side) + rule 5 + input to rule 3: manifests tracked
        # in HEAD, for THIS directory.
        tracked: list[str] = []
        for record in _git(
            ["ls-tree", "-r", "-z", "HEAD", "--", dir_rel], toplevel
        ).split("\0"):
            if not record:
                continue
            meta, _, path = record.partition("\t")
            mode = meta.split()[0]
            if not path.startswith(dir_rel + "/"):
                continue
            if mode not in {"100644", "100755"}:
                kind = "a symlink" if mode == "120000" else f"mode {mode}"
                errors.append(
                    f"{path}: is {kind} in HEAD -- manifests must be regular files"
                )
            if _is_manifest_path(path, dir_rel):
                tracked.append(path)
        # Rule 5's counterpart in the index, so a staged-but-uncommitted
        # relocation (the pre-commit view) is not read as "the path matched
        # nothing". The rule still fails when BOTH views are empty.
        staged = [
            path
            for path in _git(["ls-files", "-z", "--", dir_rel], toplevel).split("\0")
            if path and _is_manifest_path(path, dir_rel)
        ]
        if not tracked and not staged:
            errors.append(
                f"HEAD and the index both track 0 manifests under {dir_rel}/ "
                "-- refusing a vacuous pass"
            )

        # Rule 6 (working-tree side), before rule 4 reads any file in THIS
        # directory.
        non_regular = _non_regular_entries(dir_abs, dir_rel)
        errors.extend(non_regular)
        if non_regular or symlinked:
            # Rule 4 reads manifest bodies; through a symlink what it would
            # read is not the committed registry.
            per_directory[dir_name] = len(tracked) or len(staged)
            continue

        # Rule 4: one sha256 per partition path, across all manifests in
        # EVERY registry directory (`seen` is shared across the whole
        # loop, matching the original "across all manifests" scope) -- a
        # segment/errata manifest has no `partitions` key at all (D-05-07),
        # so `manifest.get("partitions", [])` contributes nothing from
        # those directories; only `manifests/` ever populates `seen`.
        for manifest_file in sorted(dir_abs.glob("**/*.json")):
            rel = manifest_file.relative_to(dir_abs).as_posix()
            if _is_pointer(rel):
                continue
            manifest = json.loads(manifest_file.read_text())
            for part in manifest.get("partitions", []):
                path, sha = part.get("path"), part.get("sha256")
                problem = partition_path_problem(path)
                if problem is not None:
                    errors.append(f"{rel}: {problem}")
                if not isinstance(path, str) or not path:
                    continue
                key = partition_path_key(path)
                prior = seen.get(key)
                if prior is None:
                    seen[key] = (sha, rel)
                elif prior[0] != sha:
                    errors.append(
                        f"partition {path} is named by {prior[1]} with sha256 "
                        f"{prior[0][:12]} and by {rel} with sha256 "
                        f"{str(sha)[:12]} -- a partition was rewritten in "
                        "place and re-manifested"
                    )
        per_directory[dir_name] = len(tracked) or len(staged)

    if not per_directory:
        # Global fallback vacuity guard (WR-07's own concern, preserved
        # across the per-directory generalization): if NONE of
        # REGISTRY_DIR_NAMES exists on disk at all under `registry_abs`,
        # the per-directory loop above skipped every name and ran Rule 5
        # nowhere -- a completely wrong `--registry-root` must still read
        # as a failure, never as "nothing to check, so it passed". A
        # registry with `manifests/` populated but `segments/`/`errata/`
        # not yet created (every commit before this plan's own) does NOT
        # hit this branch -- `per_directory` has a `"manifests"` entry.
        errors.append(
            f"none of {sorted(REGISTRY_DIR_NAMES)} exists under {registry_abs} "
            "-- refusing a vacuous pass"
        )

    # Rules 2 and 3 are CONTENT-anchored (03-FOLLOWUPS.md item 5): a D/M/T on
    # a manifest path is a violation only if that manifest's BLOB does not
    # survive at some manifest-shaped path in the destination state. A
    # content-preserving relocation is therefore legal; dropping, editing or
    # type-changing a manifest -- including inside a move -- is not.
    head_blobs = _head_manifest_blobs(toplevel)
    worktree_blobs = _worktree_manifest_blobs(toplevel)

    # Rule 2a: every commit, every parent, whole-tree walk (no pathspec).
    history = _git(
        [
            "log",
            "--no-renames",
            "--diff-merges=separate",
            "--diff-filter=DMT",
            "--format=commit %H",
            "--raw",
            "--no-abbrev",
            "-z",
            "HEAD",
        ],
        toplevel,
    )
    for commit, status, blob, path in _raw_z(history):
        realm = _realm(path) if _is_manifest_shaped(path) else None
        if status in _CHANGE_WORDS and realm is not None:
            if blob in head_blobs[realm]:
                continue  # content still present under a manifest path in its realm
            errors.append(
                f"{path}: committed manifest {_CHANGE_WORDS[status]} "
                f"in commit {commit} and its contents (blob {blob[:12]}) are "
                "not present at any manifest path in HEAD (manifests are "
                "write-once; issue a new manifest instead)"
            )

    # Rule 2b: tree comparison base -> HEAD.
    base, base_desc = resolve_base(toplevel)
    base_diff = _git(
        [
            "diff",
            "--no-renames",
            "--diff-filter=DMT",
            "--raw",
            "--no-abbrev",
            "-z",
            base,
            "HEAD",
        ],
        toplevel,
    )
    for _commit, status, blob, path in _raw_z(base_diff):
        realm = _realm(path) if _is_manifest_shaped(path) else None
        if status in _CHANGE_WORDS and realm is not None:
            if blob in head_blobs[realm]:
                continue
            errors.append(
                f"{path}: committed manifest {_CHANGE_WORDS[status]} "
                f"between {base_desc} and HEAD and its contents (blob "
                f"{blob[:12]}) are not present at any manifest path in HEAD"
            )

    # Rule 3: the working tree still holds every HEAD manifest's bytes.
    # No pathspec (WR-17): a staged registry move shows as deletes at the old
    # path before it is ever committed.
    changed = _git(
        ["diff", "--no-renames", "--raw", "--no-abbrev", "-z", "HEAD"], toplevel
    )
    for _commit, status, blob, path in _raw_z(changed):
        word = _CHANGE_WORDS.get(status)
        realm = _realm(path) if _is_manifest_shaped(path) else None
        if word is not None and realm is not None:
            if blob in worktree_blobs[realm]:
                continue
            errors.append(
                f"{path}: committed manifest {word} in the working tree and its "
                f"contents (blob {blob[:12]}) are not present at any "
                "manifest path in it"
            )

    # A merge diffed against two parents that both held the manifest reports
    # the same change twice; say it once.
    return list(dict.fromkeys(errors)), per_directory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry-root",
        default=None,
        help="override LAKE_REGISTRY_ROOT (tests point this at a scratch repo)",
    )
    args = parser.parse_args(argv)
    registry_root = (
        Path(args.registry_root) if args.registry_root else LAKE_REGISTRY_ROOT
    )

    try:
        errors, per_directory = _check_append_only_detailed(registry_root)
    except GitHistoryUnavailable as exc:
        print(f"FAIL: cannot verify manifest append-only history: {exc}")
        return 1

    if errors:
        print("FAIL: committed manifest registry is not append-only:")
        for err in errors:
            print(f"  {err}")
        return 1

    n_tracked = sum(per_directory.values())
    _base, base_desc = resolve_base(_toplevel_for(registry_root))
    print(
        f"PASS: {n_tracked} committed manifest(s) append-only against HEAD history "
        f"(content-anchored; every commit vs every parent; tree base: {base_desc})"
    )
    if per_directory:
        breakdown = ", ".join(
            f"{name}/={count}" for name, count in sorted(per_directory.items())
        )
        print(f"  by registry directory: {breakdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
