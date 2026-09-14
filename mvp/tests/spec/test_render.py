"""Tests for mvp/spec/render.py (marker-delimited regeneration) and
mvp/tools/check_spec_diff.py (CI drift/definition-change check).

Hermetic: render.* tests use small in-memory spec_md_text fixtures, never touching
the real mvp/spec.md except in the two "against the real repo state" tests explicitly
named for it (mirroring the plan's own acceptance criteria, which are about the real
file). check_spec_diff.* git-ref tests build a scratch git repo under `tmp_path`, no
dependence on this checkout's actual HEAD/history.
"""

import subprocess
from pathlib import Path

from spec.catalogue import FeatureEntry, LabelEntry
from spec.render import (
    FEATURES_BEGIN,
    FEATURES_END,
    LABELS_BEGIN,
    LABELS_END,
    render_features_table,
    render_labels_table,
    render_spec,
)
from tools.check_spec_diff import (
    check_drift,
    git_show_toml,
    main as check_spec_diff_main,
)

FEATURES = {
    "mid": FeatureEntry(
        name="mid",
        definition="(best_bid + best_ask) / 2",
        information_set="t",
        lag=0,
        normalization="none",
        source_datasets=["swap L1 BBO"],
        notes="Treat sub-tick stickiness flag separately",
        version=1,
        introduced="2026-09-13",
    ),
    "imb_top": FeatureEntry(
        name="imb_top",
        definition="(bid_size - ask_size) / (bid_size + ask_size)",
        information_set="t",
        lag=0,
        normalization="none",
        source_datasets=["swap L1 BBO"],
        notes="Undefined when both sizes are 0",
        version=1,
        introduced="2026-09-13",
    ),
}

LABELS = {
    "ret_10s_mid": LabelEntry(
        name="ret_10s_mid",
        horizon="10s",
        computation="(mid_{t+10s} - mid_t) / mid_t",
        information_set="data through t + 10s",
        embargo=">= 10s",
        notes="Primary trading target",
        version=1,
        introduced="2026-09-13",
    ),
}

SAMPLE_SPEC_MD = """# Sample spec

## Feature catalogue

<!-- catalogue:features:begin -->
| Name | Definition | Information set | Lag | Normalization | Source dataset(s) | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `placeholder` | old | old | 0 | none | old | old |
<!-- catalogue:features:end -->

**Rules:** unrelated prose that must survive untouched.

## Label catalogue

<!-- catalogue:labels:begin -->
| Name | Horizon | Computation | Information set required to evaluate | Embargo | Notes |
| --- | --- | --- | --- | --- | --- |
| `placeholder` | old | old | old | old | old |
<!-- catalogue:labels:end -->

**Rules:** more unrelated prose.
"""


def test_render_features_table_rows_only_no_header():
    body = render_features_table(FEATURES)
    assert "Name | Definition" not in body
    assert "| `mid` |" in body
    assert "| `imb_top` |" in body
    # TOML/dict declaration order preserved, not sorted
    assert body.index("`mid`") < body.index("`imb_top`")


def test_render_features_table_escapes_pipe_and_joins_source_datasets():
    entries = {
        "x": FeatureEntry(
            name="x",
            definition="a | b",
            information_set="t",
            lag=0,
            normalization="none",
            source_datasets=["ds1", "ds2"],
            notes="n",
            version=1,
            introduced="2026-09-13",
        )
    }
    body = render_features_table(entries)
    assert "a \\| b" in body
    assert "ds1, ds2" in body


def test_render_labels_table_rows_only_no_header():
    body = render_labels_table(LABELS)
    assert "Name | Horizon" not in body
    assert "| `ret_10s_mid` |" in body


def test_render_spec_replaces_only_between_markers():
    out = render_spec(SAMPLE_SPEC_MD, FEATURES, LABELS)
    assert "`placeholder`" not in out
    assert "`mid`" in out
    assert "`ret_10s_mid`" in out
    assert "unrelated prose that must survive untouched" in out
    assert "more unrelated prose" in out
    assert out.count(FEATURES_BEGIN) == 1
    assert out.count(FEATURES_END) == 1
    assert out.count(LABELS_BEGIN) == 1
    assert out.count(LABELS_END) == 1


def test_render_spec_is_idempotent():
    once = render_spec(SAMPLE_SPEC_MD, FEATURES, LABELS)
    twice = render_spec(once, FEATURES, LABELS)
    assert once == twice


def test_render_spec_against_real_repo_state_is_idempotent_on_second_run():
    """Mirrors the plan's own acceptance criterion: re-rendering the real,
    already-rendered mvp/spec.md a second time must be a no-op.
    """
    from spec.catalogue import load_features, load_labels
    from spec.render import SPEC_MD

    current = SPEC_MD.read_text()
    features = load_features()
    labels = load_labels()
    once = render_spec(current, features, labels)
    twice = render_spec(once, features, labels)
    assert once == twice


# --- check_spec_diff.py ---


def _init_scratch_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    return tmp_path


def test_git_show_toml_returns_parsed_dict_for_resolvable_ref(tmp_path):
    repo = _init_scratch_repo(tmp_path)
    toml_dir = repo / "mvp" / "spec"
    toml_dir.mkdir(parents=True)
    toml_path = toml_dir / "features.toml"
    toml_path.write_text('[mid]\ndefinition = "old"\n')
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "old"], cwd=repo, check=True)

    toml_path.write_text('[mid]\ndefinition = "new"\n')
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "new"], cwd=repo, check=True)

    old = git_show_toml("HEAD~1", "mvp/spec/features.toml", repo)
    assert old == {"mid": {"definition": "old"}}


def test_git_show_toml_returns_none_when_ref_does_not_resolve(tmp_path):
    repo = _init_scratch_repo(tmp_path)
    (repo / "mvp" / "spec").mkdir(parents=True)
    (repo / "mvp" / "spec" / "features.toml").write_text('[mid]\ndefinition = "x"\n')
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "only commit"], cwd=repo, check=True)

    # No parent commit exists yet -- HEAD~1 must not resolve.
    result = git_show_toml("HEAD~1", "mvp/spec/features.toml", repo)
    assert result is None


def test_check_drift_returns_none_when_rendering_matches():
    already_rendered = render_spec(SAMPLE_SPEC_MD, FEATURES, LABELS)

    class _Path:
        def read_text(self):
            return already_rendered

    assert check_drift(_Path(), FEATURES, LABELS) is None


def test_check_drift_returns_diff_when_rendering_differs():
    class _Path:
        def read_text(self):
            return SAMPLE_SPEC_MD

    diff = check_drift(_Path(), FEATURES, LABELS)
    assert diff is not None
    assert "mid" in diff


def test_main_warns_and_exits_zero_when_base_ref_unresolvable(
    monkeypatch, tmp_path, capsys
):
    import tools.check_spec_diff as mod

    monkeypatch.setattr(mod, "PKG_ROOT", tmp_path)
    (tmp_path / "spec").mkdir()
    monkeypatch.setattr(
        mod,
        "load_features",
        lambda: FEATURES,
    )
    monkeypatch.setattr(mod, "load_labels", lambda: LABELS)
    # Real committed mvp/spec.md already matches its own TOML (checked by the repo
    # tests above); here we just need *some* spec.md that renders driftlessly against
    # the monkeypatched FEATURES/LABELS, so build one from render_spec directly.
    spec_md = render_spec(SAMPLE_SPEC_MD, FEATURES, LABELS)
    (tmp_path / "spec.md").write_text(spec_md)

    exit_code = check_spec_diff_main(["--base-ref", "HEAD~999"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "does not resolve" in captured.out.lower() or "WARN" in captured.out


def _commit(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def test_diff_removed_names_flags_deleted_entry():
    from spec.catalogue import diff_removed_names

    old = {"mid": {"definition": "x"}, "imb_top": {"definition": "y"}}
    new = {"imb_top": {"definition": "y"}}
    assert diff_removed_names(old, new) == ["mid"]


def test_spec_diff_main_fails_when_feature_removed(monkeypatch, tmp_path, capsys):
    repo = _init_scratch_repo(tmp_path)
    (repo / "mvp" / "spec").mkdir(parents=True)
    features_path = repo / "mvp" / "spec" / "features.toml"
    features_path.write_text('[mid]\ndefinition = "old"\n')
    (repo / "mvp" / "spec" / "labels.toml").write_text("")
    _commit(repo, "base")

    features_path.write_text("")  # mid removed outright
    _commit(repo, "remove mid")

    import tools.check_spec_diff as mod

    monkeypatch.setattr(mod, "PKG_ROOT", repo / "mvp")
    monkeypatch.setattr(mod, "load_features", lambda: {})
    monkeypatch.setattr(mod, "load_labels", lambda: {})
    spec_md = (
        "<!-- catalogue:features:begin -->\n"
        "| Name | Definition | Information set (latest input timestamp ≤ decision `t`) "
        "| Lag (if any) | Normalization | Source dataset(s) | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "<!-- catalogue:features:end -->\n"
        "<!-- catalogue:labels:begin -->\n"
        "| Name | Horizon | Computation | Information set required to evaluate "
        "| Embargo | Notes |\n"
        "| --- | --- | --- | --- | --- |\n"
        "<!-- catalogue:labels:end -->\n"
    )
    (repo / "mvp" / "spec.md").write_text(spec_md)

    exit_code = check_spec_diff_main(["--base-ref", "HEAD~1"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "mid" in captured.out


def test_default_base_ref_resolves_to_merge_base_with_develop(tmp_path):
    repo = _init_scratch_repo(tmp_path)
    (repo / "f.txt").write_text("base\n")
    _commit(repo, "base")
    subprocess.run(["git", "branch", "develop"], cwd=repo, check=True)

    (repo / "f.txt").write_text("commit1\n")
    _commit(repo, "commit 1")
    (repo / "f.txt").write_text("commit2\n")
    _commit(repo, "commit 2")

    from tools.check_spec_diff import _default_base_ref

    base_ref = _default_base_ref(repo)
    expected = subprocess.run(
        ["git", "merge-base", "HEAD", "develop"],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert base_ref == expected
