"""Standalone MLflow smoke-run script -- proves ROADMAP criterion 5 for real.

Run ONCE, by hand, by the executor:

    cd mvp && ./.venv/bin/python3 -m tracking.smoke_run

NEVER invoked by pytest and NEVER wired into CI: CI runs on `ubuntu-latest`
where `/Volumes/ProjectsSSD` does not exist, and `validate_data_root` would
(correctly) refuse it there. This script is deliberately outside `mvp/tests/`
and has no `test_` prefix so it is never collected.

Logs one dummy metric against the real SQLite backend at
`/Volumes/ProjectsSSD/aihedgefund/mlflow/`, then reads the run back via
`MlflowClient.get_run` and prints the run_id + all 8 mandatory tags -- paste
this stdout into the plan's SUMMARY as evidence.
"""

from __future__ import annotations

import pathlib

import mlflow
from mlflow.tracking import MlflowClient

from tracking.mlflow_utils import (
    build_tracking_uri,
    compute_code_hash,
    compute_env_hash,
    start_tracked_run,
)

PKG_ROOT = pathlib.Path(__file__).resolve().parents[1]
MLFLOW_ROOT = "/Volumes/ProjectsSSD/aihedgefund/mlflow"


def main() -> int:
    root = pathlib.Path(MLFLOW_ROOT)
    root.mkdir(parents=True, exist_ok=True)

    tags = dict(
        code_hash=compute_code_hash(),
        data_hash="none",
        seed="0",
        env_hash=compute_env_hash(PKG_ROOT / "uv.lock"),
        segment_manifest_id="n/a",
        model_class="n/a",
        fold_config="n/a",
        stage="n/a",
    )

    run = start_tracked_run(MLFLOW_ROOT, tags, "phase2-smoke")
    run_id = run.info.run_id
    mlflow.log_metric("smoke", 1.0)
    mlflow.end_run()

    tracking_uri = build_tracking_uri(MLFLOW_ROOT)
    client = MlflowClient(tracking_uri)
    fetched = client.get_run(run_id)

    print(f"run_id: {run_id}")
    for key in sorted(tags):
        print(f"  {key}: {fetched.data.tags[key]}")

    mvp_mlruns = PKG_ROOT / "mlruns"
    assert not mvp_mlruns.exists(), (
        f"unexpected mlruns/ directory created under mvp/ at {mvp_mlruns}"
    )
    print("confirmed: no mlruns/ directory created under mvp/")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
