"""CI-callable guardrail: fail if mvp/spec.md's rendered catalogue tables drift from
mvp/spec/{features,labels}.toml, or if a feature's `definition` / a label's
`computation` changed under an existing name relative to `--base-ref`.

Invoked as `uv run --directory mvp python -m tools.check_spec_diff` (process cwd =
mvp/) by both pre-commit and GitHub Actions (Plan 04 wires the callers; this module
only needs to be importable and directly testable here).

PATH RESOLUTION RULE: `PKG_ROOT = Path(__file__).resolve().parents[1]` anchors every
ON-DISK path (`PKG_ROOT / "spec.md"`, `PKG_ROOT / "spec" / "features.toml"`) --
never a bare relative `"mvp/spec.md"` literal, which would resolve to the nonexistent
`mvp/mvp/spec.md` when process cwd is already `mvp/`. `git show <ref>:mvp/spec/...` is
a git-relative PATHSPEC, correct regardless of cwd, evaluated with cwd=PKG_ROOT so it
resolves against this repo's history even if invoked from elsewhere.
"""

from __future__ import annotations

import argparse
import difflib
import subprocess
import tomllib
from pathlib import Path

from spec.catalogue import (
    diff_definition_changes,
    diff_removed_names,
    load_features,
    load_labels,
)
from spec.render import render_spec
from tools.git_env import scrubbed_git_env

PKG_ROOT = Path(__file__).resolve().parents[1]

FEATURES_TOML_RELPATH = "mvp/spec/features.toml"
LABELS_TOML_RELPATH = "mvp/spec/labels.toml"


def git_show_toml(ref: str, relpath: str, cwd: Path) -> dict | None:
    """Return the parsed TOML at `ref:relpath`, or None if the ref/path doesn't
    resolve (e.g. shallow history with no parent commit) -- the caller treats this
    as "skip, warn", never "fail".
    """
    result = subprocess.run(
        ["git", "show", f"{ref}:{relpath}"],
        capture_output=True,
        cwd=cwd,
        text=True,
        env=scrubbed_git_env(),
    )
    if result.returncode != 0:
        return None
    return tomllib.loads(result.stdout)


def check_drift(spec_md_path: Path, features: dict, labels: dict) -> str | None:
    """Return a unified diff if re-rendering `spec_md_path` from `features`/`labels`
    would change it, else None.
    """
    current = spec_md_path.read_text()
    rendered = render_spec(current, features, labels)
    if rendered == current:
        return None
    return "\n".join(
        difflib.unified_diff(
            current.splitlines(),
            rendered.splitlines(),
            fromfile="spec.md (committed)",
            tofile="spec.md (re-rendered from TOML)",
            lineterm="",
        )
    )


def _default_base_ref(cwd: Path) -> str:
    """Resolve the default `--base-ref`: the merge-base of HEAD with `develop`
    (falling back to `origin/develop`, then finally `HEAD~1` with a WARN if
    neither branch ref resolves -- e.g. a fresh clone with no local `develop`
    and no remote configured).

    A plain `HEAD~1` default only ever diffs against the immediately
    preceding commit, which a two-commit sequence (delete a name in commit N,
    re-add it under the same name with a different definition in commit N+1)
    defeats: at N+1, `HEAD~1` is N, which no longer has the name, so it looks
    brand-new. The merge-base with the branch's start point is stable across
    however many commits the branch/PR has, closing that gap.

    If the resolved merge-base IS HEAD (e.g. running directly on `develop`
    itself -- a post-merge CI `push:` run, or any direct push to develop),
    diffing HEAD against itself would silently no-op the whole check. Falls
    back to `HEAD~1` in that case instead, matching this module's prior
    (pre-CR-08) behavior on that one branch so this fix is a strict widening,
    never a narrowing, of what gets caught.
    """
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        cwd=cwd,
        text=True,
        env=scrubbed_git_env(),
    )
    head_sha = head.stdout.strip() if head.returncode == 0 else None

    for ref in ("develop", "origin/develop"):
        result = subprocess.run(
            ["git", "merge-base", "HEAD", ref],
            capture_output=True,
            cwd=cwd,
            text=True,
            env=scrubbed_git_env(),
        )
        if result.returncode == 0 and result.stdout.strip():
            merge_base = result.stdout.strip()
            if head_sha is not None and merge_base == head_sha:
                continue  # HEAD IS the merge-base -- diffing it against itself is a no-op
            return merge_base
    print(
        "WARN: could not resolve a merge-base with 'develop'/'origin/develop' that "
        "differs from HEAD -- falling back to HEAD~1 (single-commit-back default)."
    )
    return "HEAD~1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref",
        default=None,
        help="git ref to diff the catalogue TOML against "
        "(default: merge-base of HEAD with develop/origin-develop, "
        "falling back to HEAD~1)",
    )
    args = parser.parse_args(argv)
    base_ref = (
        args.base_ref if args.base_ref is not None else _default_base_ref(PKG_ROOT)
    )

    exit_code = 0

    features = load_features()
    labels = load_labels()

    drift = check_drift(PKG_ROOT / "spec.md", features, labels)
    if drift is not None:
        print("FAIL: mvp/spec.md's catalogue tables drifted from the TOML source:")
        print(drift)
        exit_code = 1

    old_features = git_show_toml(base_ref, FEATURES_TOML_RELPATH, PKG_ROOT)
    old_labels = git_show_toml(base_ref, LABELS_TOML_RELPATH, PKG_ROOT)

    if old_features is None or old_labels is None:
        print(
            f"WARN: base-ref {base_ref!r} does not resolve "
            f"{FEATURES_TOML_RELPATH!r}/{LABELS_TOML_RELPATH!r} -- skipping "
            "definition-drift check (not a failure)."
        )
        return exit_code

    new_features_raw = tomllib.loads((PKG_ROOT / "spec" / "features.toml").read_text())
    new_labels_raw = tomllib.loads((PKG_ROOT / "spec" / "labels.toml").read_text())

    changed_features = diff_definition_changes(
        old_features, new_features_raw, field="definition"
    )
    changed_labels = diff_definition_changes(
        old_labels, new_labels_raw, field="computation"
    )

    if changed_features or changed_labels:
        print(
            "FAIL: definition/computation changed under an existing name -- give it "
            "a new name instead:"
        )
        if changed_features:
            print(f"  features (definition changed): {changed_features}")
        if changed_labels:
            print(f"  labels (computation changed): {changed_labels}")
        exit_code = 1

    removed_features = diff_removed_names(old_features, new_features_raw)
    removed_labels = diff_removed_names(old_labels, new_labels_raw)

    if removed_features or removed_labels:
        print(
            "FAIL: catalogue entry removed outright -- old runs referencing it "
            "become unreproducible. Add `deprecated = true` to the entry instead "
            "of deleting it:"
        )
        if removed_features:
            print(f"  features (removed): {removed_features}")
        if removed_labels:
            print(f"  labels (removed): {removed_labels}")
        exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
