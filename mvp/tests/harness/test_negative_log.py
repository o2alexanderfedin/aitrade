"""Tests for harness.negative_log: the MLflow-first negative-result log
(D-05-22, EVAL-04) and its CLI (tools/harness_negative_log_cli.py)."""

from __future__ import annotations

import logging
import os

import pytest
from data.lake_paths import MLFLOW_TRACKING_ROOT_ENV
from data.store import compute_manifest_id
from harness.negative_log import (
    NegativeLogError,
    config_fingerprint,
    query_negative_results,
    record_negative_result,
    warn_if_already_negative,
)
from tools.harness_negative_log_cli import main as cli_main

#: Every mandatory tag key `tracking.mlflow_utils.start_tracked_run`
#: requires EXCEPT `stage` -- `record_negative_result` fills that one in
#: itself (mirroring `harness.budget.record_look`'s own `stage`/
#: `segment_manifest_id` split, except a negative result has no segment of
#: its own, so `segment_manifest_id` is caller-supplied here, not derived).
RUN_TAGS = {
    "code_hash": "a" * 40,
    "data_hash": "none",
    "seed": "0",
    "env_hash": "b" * 64,
    "model_class": "none (harness probe, no model in this phase)",
    "fold_config": "5seg",
    "segment_manifest_id": "none",
}


def test_config_fingerprint_matches_compute_manifest_id():
    configs = [
        {"layout": "5seg", "budget_allowance": 1},
        {"a": 1, "b": [1, 2, 3]},
        {},
        {"nested": {"z": 1, "a": 2}, "list": [3, 2, 1]},
    ]
    for config in configs:
        assert config_fingerprint(config) == compute_manifest_id(config)


def test_record_negative_result_tags_outcome_and_reason(tracking_root):
    from mlflow.tracking import MlflowClient
    from tracking.mlflow_utils import build_tracking_uri

    config = {"layout": "5seg", "budget_allowance": 1}
    run_id = record_negative_result(
        config,
        reason="loss diverged after 3 epochs",
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )
    client = MlflowClient(build_tracking_uri(str(tracking_root)))
    run = client.get_run(run_id)
    assert run.data.tags["stage"] == "negative_result"
    assert run.data.tags["outcome"] == "negative"
    assert run.data.tags["reason"] == "loss diverged after 3 epochs"
    assert run.data.tags["config_fingerprint"] == config_fingerprint(config)


def test_record_negative_result_refuses_an_over_long_reason(tracking_root):
    from harness.negative_log import NegativeLogError
    from mlflow.utils.validation import MAX_TAG_VAL_LENGTH

    with pytest.raises(NegativeLogError, match="exceeding"):
        record_negative_result(
            {"layout": "5seg"},
            reason="x" * (MAX_TAG_VAL_LENGTH + 1),
            tracking_root=str(tracking_root),
            run_tags=dict(RUN_TAGS),
        )


def test_query_negative_results_returns_queryable_rows(tracking_root):
    config1 = {"layout": "5seg"}
    config2 = {"layout": "compressed_3seg"}
    run_id1 = record_negative_result(
        config1,
        reason="loss diverged",
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )
    run_id2 = record_negative_result(
        config2,
        reason="nan loss",
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS, code_hash="c" * 40),
    )

    rows = query_negative_results(tracking_root=str(tracking_root))
    assert len(rows) == 2
    by_run_id = {row[3]: row for row in rows}

    fp1, reason1, when1, rid1, code_hash1, data_hash1 = by_run_id[run_id1]
    assert fp1 == config_fingerprint(config1)
    assert reason1 == "loss diverged"
    assert isinstance(when1, int)
    assert rid1 == run_id1
    assert code_hash1 == "a" * 40
    assert data_hash1 == "none"

    fp2, reason2, _when2, rid2, code_hash2, _data_hash2 = by_run_id[run_id2]
    assert fp2 == config_fingerprint(config2)
    assert reason2 == "nan loss"
    assert rid2 == run_id2
    assert code_hash2 == "c" * 40

    # Anti-vacuity: the two fingerprints genuinely differ (different
    # configs), proving the rows are not accidentally identical.
    assert fp1 != fp2


def test_query_negative_results_filters_by_fingerprint(tracking_root):
    config1 = {"layout": "5seg"}
    config2 = {"layout": "compressed_3seg"}
    record_negative_result(
        config1,
        reason="loss diverged",
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )
    record_negative_result(
        config2,
        reason="nan loss",
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )

    fp1 = config_fingerprint(config1)
    rows = query_negative_results(
        tracking_root=str(tracking_root), config_fingerprint=fp1
    )
    assert len(rows) == 1
    assert rows[0][0] == fp1
    assert rows[0][1] == "loss diverged"


def test_rerun_of_a_known_negative_fingerprint_warns_not_refuses(tracking_root, caplog):
    config = {"layout": "5seg"}
    record_negative_result(
        config,
        reason="loss diverged",
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )

    with caplog.at_level(logging.WARNING, logger="harness.negative_log"):
        result = warn_if_already_negative(config, tracking_root=str(tracking_root))

    # Never raises -- reaching this line, with a normal return value,
    # proves it proceeded rather than refusing.
    assert result is None
    assert "loss diverged" in caplog.text
    assert config_fingerprint(config) in caplog.text


def test_warn_if_already_negative_is_silent_for_an_unrecorded_config(
    tracking_root, caplog
):
    with caplog.at_level(logging.WARNING, logger="harness.negative_log"):
        warn_if_already_negative(
            {"layout": "never_recorded"}, tracking_root=str(tracking_root)
        )
    assert caplog.text == ""


def test_cli_lists_all_negative_results(tracking_root, capsys):
    config1 = {"layout": "5seg"}
    config2 = {"layout": "compressed_3seg"}
    record_negative_result(
        config1,
        reason="loss diverged",
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )
    record_negative_result(
        config2,
        reason="nan loss",
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
    )

    exit_code = cli_main(["--tracking-root", str(tracking_root)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "loss diverged" in out
    assert "nan loss" in out

    fp1 = config_fingerprint(config1)
    exit_code = cli_main(["--tracking-root", str(tracking_root), "--fingerprint", fp1])
    assert exit_code == 0
    filtered_out = capsys.readouterr().out
    assert "loss diverged" in filtered_out
    assert "nan loss" not in filtered_out


def test_warn_if_already_negative_propagates_a_query_failure(tmp_path):
    """D-05-12's own honesty rule, applied to the warn path: a query
    failure must never become silence. `warn_if_already_negative` proceeds
    (never raises) ONLY when the underlying query genuinely succeeded and
    found nothing -- an uninitialised/non-canonical store must surface
    UNCHANGED through `warn_if_already_negative` -> `query_negative_results`,
    exactly as `harness.budget.look_count`'s own propagation contract
    requires (mirrors test_budget.py::
    test_look_count_propagates_a_query_failure's shape)."""
    empty_root = tmp_path / "no_store_here"
    empty_root.mkdir()
    os.environ[MLFLOW_TRACKING_ROOT_ENV] = str(empty_root)

    with pytest.raises(NegativeLogError) as exc_info:
        warn_if_already_negative({"layout": "5seg"}, tracking_root=str(empty_root))
    # Identity, not isinstance: a future wrap into a plain ValueError
    # (NegativeLogError's own base class) must fail this assertion.
    assert exc_info.type is NegativeLogError
    assert "mlflow.db" in str(exc_info.value)


# --------------------------------------------------------------------------
# 05-REVIEW.md WR-04: filter_string values are validated BEFORE any MLflow
# query, not spliced in raw
# --------------------------------------------------------------------------


def test_query_negative_results_refuses_a_hostile_config_fingerprint(tracking_root):
    hostile = "fp' or tags.stage != 'x"
    with pytest.raises(NegativeLogError, match="filter_string"):
        query_negative_results(
            tracking_root=str(tracking_root), config_fingerprint=hostile
        )
