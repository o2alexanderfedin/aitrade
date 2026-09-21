"""Tests for harness.budget: the MLflow-first durable look counter
(D-05-11..15)."""

from __future__ import annotations

import os

import pytest
from data.lake_paths import MLFLOW_TRACKING_ROOT_ENV
from harness.budget import BudgetError, look_count, record_look
from mlflow.tracking import MlflowClient
from tracking.mlflow_utils import build_tracking_uri

BASE_RUN_TAGS = {
    "code_hash": "a" * 40,
    "data_hash": "none",
    "seed": "0",
    "env_hash": "b" * 64,
    "model_class": "none (harness probe, no model in this phase)",
    "fold_config": "5seg",
}


def test_look_count_is_zero_before_any_run(tracking_root):
    assert look_count("mid-1", "val_s1", tracking_root=str(tracking_root)) == 0


def test_look_count_propagates_a_query_failure(tmp_path):
    empty_root = tmp_path / "no_store_here"
    empty_root.mkdir()
    # Make the odd root canonical (WR-04's pin lives on the env var), so
    # this test is about the store-shape refusal, not the canonical-root one.
    os.environ[MLFLOW_TRACKING_ROOT_ENV] = str(empty_root)

    with pytest.raises(BudgetError, match="mlflow.db"):
        look_count("mid-1", "val_s1", tracking_root=str(empty_root))


def test_record_look_increments_and_is_queryable(tracking_root):
    run_id = record_look(
        "mid-1",
        "val_s1",
        tracking_root=str(tracking_root),
        run_tags=dict(BASE_RUN_TAGS),
    )
    assert run_id
    assert look_count("mid-1", "val_s1", tracking_root=str(tracking_root)) == 1

    client = MlflowClient(build_tracking_uri(str(tracking_root)))
    run = client.get_run(run_id)
    assert run.data.tags["segment_manifest_id"] == "mid-1"
    assert run.data.tags["fold_config"] == "5seg"
    assert run.data.tags["stage"] == "val_look"
    assert run.data.tags["segment_name"] == "val_s1"


def test_record_look_requires_fold_config_already_set(tracking_root):
    tags = {k: v for k, v in BASE_RUN_TAGS.items() if k != "fold_config"}
    with pytest.raises(BudgetError, match="fold_config"):
        record_look("mid-1", "val_s1", tracking_root=str(tracking_root), run_tags=tags)


def test_look_count_granularity_is_manifest_and_segment_name_not_model_class(
    tracking_root,
):
    tags_a = dict(BASE_RUN_TAGS, model_class="regression")
    tags_b = dict(BASE_RUN_TAGS, model_class="trees")
    record_look("mid-1", "val_s1", tracking_root=str(tracking_root), run_tags=tags_a)
    record_look("mid-1", "val_s1", tracking_root=str(tracking_root), run_tags=tags_b)
    assert look_count("mid-1", "val_s1", tracking_root=str(tracking_root)) == 2
    assert look_count("mid-1", "val_s2", tracking_root=str(tracking_root)) == 0
