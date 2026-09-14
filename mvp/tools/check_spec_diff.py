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

from spec.catalogue import diff_definition_changes, load_features, load_labels
from spec.render import render_spec

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref",
        default="HEAD~1",
        help="git ref to diff the catalogue TOML against (default: HEAD~1)",
    )
    args = parser.parse_args(argv)

    exit_code = 0

    features = load_features()
    labels = load_labels()

    drift = check_drift(PKG_ROOT / "spec.md", features, labels)
    if drift is not None:
        print("FAIL: mvp/spec.md's catalogue tables drifted from the TOML source:")
        print(drift)
        exit_code = 1

    old_features = git_show_toml(args.base_ref, FEATURES_TOML_RELPATH, PKG_ROOT)
    old_labels = git_show_toml(args.base_ref, LABELS_TOML_RELPATH, PKG_ROOT)

    if old_features is None or old_labels is None:
        print(
            f"WARN: base-ref {args.base_ref!r} does not resolve "
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

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
