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
2. No manifest (anything under `manifests/` outside `by-date/` pointer
   directories) may ever have been DELETED or MODIFIED in the history of
   `HEAD` (`git log --diff-filter=DM --no-renames`; a rename counts as a
   delete). Manifests are write-once: a rebuild issues a NEW manifest and
   new partition files, and the old manifest keeps resolving.
3. Every manifest tracked in `HEAD` must exist in the working tree with
   bytes identical to the committed blob (catches the uncommitted delete /
   edit before it is ever committed; in a pre-commit run the working tree is
   the content being committed).
4. No two manifests in the working tree may name the same
   `partitions[].path` with different `sha256` -- the in-place rewrite plus
   a reissued manifest, even when the old manifest is kept.
5. `HEAD` must track at least one manifest: a path bug that matched nothing
   must not read as "nothing was rewritten".

`by-date/` index files are mutable pointers by design (a rebuild repoints
them) and are excluded from rules 2-4.

Needs no mounted lake -- manifest JSON is git-committed -- so it runs
identically in pre-commit and on every CI runner.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from data.lake_paths import LAKE_REGISTRY_ROOT
from tools.git_env import scrubbed_git_env

PKG_ROOT = Path(__file__).resolve().parents[1]

POINTER_DIR_PREFIX = "by-date"


class GitHistoryUnavailable(RuntimeError):
    """Raised when git history cannot be consulted honestly (not a repo,
    shallow clone, git failure)."""


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=scrubbed_git_env(),
    )
    if result.returncode != 0:
        raise GitHistoryUnavailable(
            f"git {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout


def _is_pointer(rel_to_manifests: str) -> bool:
    return any(
        part == POINTER_DIR_PREFIX or part.startswith(POINTER_DIR_PREFIX + "-")
        for part in rel_to_manifests.split("/")[:-1]
    )


def _is_manifest_path(repo_rel: str, manifests_rel: str) -> bool:
    if not repo_rel.startswith(manifests_rel + "/") or not repo_rel.endswith(".json"):
        return False
    return not _is_pointer(repo_rel[len(manifests_rel) + 1 :])


def check_append_only(registry_root: Path) -> tuple[list[str], int]:
    """Return `(violations, n_manifests_tracked_in_HEAD)`; an empty violation
    list means append-only holds. Raises `GitHistoryUnavailable` if history
    cannot be consulted."""
    manifests_dir = Path(registry_root).resolve() / "manifests"
    probe_dir = (
        manifests_dir if manifests_dir.exists() else Path(registry_root).resolve()
    )
    while not probe_dir.exists():
        probe_dir = probe_dir.parent
    toplevel = Path(_git(["rev-parse", "--show-toplevel"], probe_dir).strip()).resolve()

    if _git(["rev-parse", "--is-shallow-repository"], toplevel).strip() == "true":
        raise GitHistoryUnavailable(
            "shallow clone: manifest history is incomplete, so append-only "
            "cannot be verified. Fetch full history (actions/checkout "
            "`fetch-depth: 0`, or `git fetch --unshallow`)."
        )

    manifests_rel = manifests_dir.relative_to(toplevel).as_posix()
    errors: list[str] = []

    # Rule 5 + input to rule 3: manifests tracked in HEAD.
    tracked = [
        line
        for line in _git(
            ["ls-tree", "-r", "--name-only", "HEAD", "--", manifests_rel], toplevel
        ).splitlines()
        if _is_manifest_path(line, manifests_rel)
    ]
    if not tracked:
        errors.append(
            f"HEAD tracks 0 manifests under {manifests_rel}/ -- refusing a vacuous pass"
        )
        return errors, 0

    # Rule 2: never deleted or modified anywhere in HEAD's history.
    history = _git(
        [
            "log",
            "--no-renames",
            "--diff-filter=DM",
            "--format=commit %H",
            "--name-status",
            "HEAD",
            "--",
            manifests_rel,
        ],
        toplevel,
    )
    commit = "?"
    for line in history.splitlines():
        if line.startswith("commit "):
            commit = line.split()[1][:12]
            continue
        fields = line.split("\t")
        if len(fields) == 2 and _is_manifest_path(fields[1], manifests_rel):
            action = "deleted" if fields[0] == "D" else "modified"
            errors.append(
                f"{fields[1]}: committed manifest {action} in commit {commit} "
                "(manifests are write-once; issue a new manifest instead)"
            )

    # Rule 3: working tree still has every HEAD manifest, byte-identical.
    changed = _git(
        ["diff", "--no-renames", "--name-status", "HEAD", "--", manifests_rel],
        toplevel,
    )
    for line in changed.splitlines():
        fields = line.split("\t")
        if len(fields) != 2 or not _is_manifest_path(fields[1], manifests_rel):
            continue
        if fields[0] == "D":
            errors.append(
                f"{fields[1]}: committed manifest deleted in the working tree"
            )
        elif fields[0] == "M":
            errors.append(
                f"{fields[1]}: committed manifest modified in the working tree"
            )

    # Rule 4: one sha256 per partition path across all manifests.
    seen: dict[str, tuple[str, str]] = {}
    for manifest_file in sorted(manifests_dir.glob("**/*.json")):
        rel = manifest_file.relative_to(manifests_dir).as_posix()
        if _is_pointer(rel):
            continue
        manifest = json.loads(manifest_file.read_text())
        for part in manifest.get("partitions", []):
            path, sha = part.get("path"), part.get("sha256")
            prior = seen.get(path)
            if prior is None:
                seen[path] = (sha, rel)
            elif prior[0] != sha:
                errors.append(
                    f"partition {path} is named by {prior[1]} with sha256 "
                    f"{prior[0][:12]} and by {rel} with sha256 {str(sha)[:12]} "
                    "-- a partition was rewritten in place and re-manifested"
                )

    return errors, len(tracked)


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
        errors, n_tracked = check_append_only(registry_root)
    except GitHistoryUnavailable as exc:
        print(f"FAIL: cannot verify manifest append-only history: {exc}")
        return 1

    if errors:
        print("FAIL: committed manifest registry is not append-only:")
        for err in errors:
            print(f"  {err}")
        return 1

    print(f"PASS: {n_tracked} committed manifest(s) append-only against HEAD history")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
