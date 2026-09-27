"""Subprocess-level proof that `mlflow-skinny` never imports pandas.

Runs in a fresh `sys.executable` subprocess (not the pytest process) so import
order/plugin side effects can't hide a lazy pandas import. Exercises BOTH:
  - Stage A: a bare `import mlflow` (the case 02-RESEARCH.md's Pitfall 1
    verified directly against full `mlflow`).
  - Stage B: the full tracking cycle (set_tracking_uri -> create_experiment ->
    start_run -> log_metric -> end_run -> MlflowClient.get_run) against a
    tmp_path-backed SQLite file, closing 02-RESEARCH.md's Assumption A1 --
    the residual risk that a heavier SQL-store code path (not exercised by a
    bare import) could lazily pull pandas in.

Both stages run in the SAME subprocess invocation so a regression in either
cannot hide behind the other passing.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys

_SUBPROCESS_SCRIPT = """
import sys

# --- Stage A: bare import ---
import mlflow
assert "pandas" not in sys.modules, "STAGE A: pandas imported by bare `import mlflow`"

# --- Stage B: full tracking cycle against a tmp_path SQLite backend ---
from mlflow.tracking import MlflowClient

tracking_uri = "sqlite:///{sqlite_path}"
mlflow.set_tracking_uri(tracking_uri)
experiment_id = mlflow.create_experiment("subprocess-check")
run = mlflow.start_run(experiment_id=experiment_id, tags={{"k": "v"}})
mlflow.log_metric("x", 1.0)
run_id = run.info.run_id
mlflow.end_run()

client = MlflowClient(tracking_uri)
fetched = client.get_run(run_id)
assert fetched.data.tags["k"] == "v", "STAGE B: tag round-trip failed"
assert fetched.data.metrics["x"] == 1.0, "STAGE B: metric round-trip failed"

assert "pandas" not in sys.modules, (
    "STAGE B: pandas imported during the full start_run/log_metric/end_run/"
    "get_run cycle"
)

print("OK: both stages passed, pandas never imported")
"""


def test_mlflow_never_imports_pandas_bare_import_and_full_cycle(tmp_path):
    sqlite_path = tmp_path / "mlflow.db"
    script = _SUBPROCESS_SCRIPT.format(sqlite_path=sqlite_path)

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"subprocess failed (stage identified in stderr):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "OK: both stages passed" in result.stdout


def test_pandas_not_installed_at_all():
    """Secondary assertion: pandas was never installed in the project venv,
    not merely unimported. If this ever disagrees with the subprocess-level
    `sys.modules` check above, the subprocess check is authoritative --
    a transitive test-only dependency could install pandas without any
    project code ever importing it, which is a different (lower) risk than
    mlflow-skinny itself pulling it in."""
    assert importlib.util.find_spec("pandas") is None, (
        "pandas is installed in the project venv -- if this was pulled in by "
        "a legitimate test-only dependency (not mlflow-skinny), the "
        "subprocess sys.modules check above remains the binding assertion "
        "for the no-pandas mandate"
    )
