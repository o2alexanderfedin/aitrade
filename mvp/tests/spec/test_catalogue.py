"""Tests for mvp/spec/catalogue.py -- the typed feature/label catalogue registry.

Hermetic: reads only the committed mvp/spec/{features,labels}.toml and small
in-memory fixtures constructed inline; no host disk state, no network.
"""

import pytest

from spec.catalogue import (
    CatalogueError,
    diff_definition_changes,
    get_feature,
    get_label,
    load_features,
    load_labels,
)


def test_load_features_returns_all_seed_entries():
    features = load_features()
    assert set(features) == {"mid", "imb_top", "ofi", "trade_flow"}


def test_load_features_raises_on_missing_key(tmp_path):
    bad = tmp_path / "features.toml"
    bad.write_text('[mid]\ndefinition = "(best_bid + best_ask) / 2"\n')
    with pytest.raises(CatalogueError, match="mid"):
        load_features(bad)


def test_load_labels_returns_all_seed_entries():
    labels = load_labels()
    assert set(labels) == {
        "ret_10s_mid",
        "ret_1s_mid",
        "ret_1min_mid",
        "ret_10min_mid",
    }


def test_load_labels_raises_on_missing_key(tmp_path):
    bad = tmp_path / "labels.toml"
    bad.write_text('[ret_10s_mid]\nhorizon = "10s"\n')
    with pytest.raises(CatalogueError, match="ret_10s_mid"):
        load_labels(bad)


def test_get_feature_returns_entry_with_expected_definition():
    entry = get_feature("mid")
    assert entry.definition == "(best_bid + best_ask) / 2"
    assert entry.name == "mid"


def test_get_feature_unknown_name_raises():
    with pytest.raises(CatalogueError, match="nonexistent"):
        get_feature("nonexistent")


def test_get_label_returns_entry():
    entry = get_label("ret_10s_mid")
    assert entry.horizon == "10s"
    assert entry.name == "ret_10s_mid"


def test_get_label_unknown_name_raises():
    with pytest.raises(CatalogueError, match="nonexistent"):
        get_label("nonexistent")


def test_diff_definition_changes_flags_changed_definition_default_field():
    old = {"mid": {"definition": "(best_bid + best_ask) / 2"}}
    new = {"mid": {"definition": "(best_bid + best_ask) / 2 + 1"}}
    assert diff_definition_changes(old, new) == ["mid"]


def test_diff_definition_changes_ignores_unchanged_definition():
    old = {"mid": {"definition": "(best_bid + best_ask) / 2"}}
    new = {"mid": {"definition": "(best_bid + best_ask) / 2"}}
    assert diff_definition_changes(old, new) == []


def test_diff_definition_changes_ignores_brand_new_entry():
    old: dict = {}
    new = {"mid": {"definition": "(best_bid + best_ask) / 2"}}
    assert diff_definition_changes(old, new) == []


def test_diff_definition_changes_labels_field_computation():
    old = {"ret_10s_mid": {"computation": "(mid_{t+10s} - mid_t) / mid_t"}}
    new = {"ret_10s_mid": {"computation": "(mid_{t+10s} - mid_t) / mid_t + 1"}}
    assert diff_definition_changes(old, new, field="computation") == ["ret_10s_mid"]


def test_diff_definition_changes_labels_unchanged_computation_not_flagged():
    old = {"ret_10s_mid": {"computation": "(mid_{t+10s} - mid_t) / mid_t"}}
    new = {"ret_10s_mid": {"computation": "(mid_{t+10s} - mid_t) / mid_t"}}
    assert diff_definition_changes(old, new, field="computation") == []
