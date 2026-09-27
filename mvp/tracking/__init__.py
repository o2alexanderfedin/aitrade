"""MLflow tracking wrapper: the only place a run is started.

`mlflow_utils.py` enforces the 8-key mandatory tag schema declared in
`mvp/spec.md`'s "MLflow tag schema" section before any `mlflow.start_run` call,
and reuses Phase 1's `validate_data_root` for the tracking root guard (no
second cloud-sync/free-space check is written). See `mvp/spec.md` for the
tag schema contract and `mvp/tracking/smoke_run.py` for the standalone,
pytest-excluded script that proves it end-to-end against the real SQLite
backend.
"""
