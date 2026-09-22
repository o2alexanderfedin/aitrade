"""CLI: list recorded negative-result configurations (D-05-22, EVAL-04).

A manual inspection tool -- NOT wired into any pre-commit hook. Run by
hand against the real, canonical MLflow tracking root (the default), or,
for testing/demo, a `--tracking-root` pointed at a `tmp_path` store (the
canonical-root pin in `harness.negative_log` means any OTHER value is
refused unless `$AIHF_MLFLOW_TRACKING_ROOT` is also pointed at it).

Usage:
    python -m tools.harness_negative_log_cli
    python -m tools.harness_negative_log_cli --tracking-root /path/to/mlflow
    python -m tools.harness_negative_log_cli --fingerprint <sha256-hex>
"""

from __future__ import annotations

import argparse
import sys

from data import lake_paths
from harness.negative_log import query_negative_results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List recorded negative-result configurations (D-05-22)."
    )
    parser.add_argument(
        "--tracking-root",
        default=None,
        help="MLflow tracking root (default: the canonical project root, "
        "$AIHF_MLFLOW_TRACKING_ROOT or the built-in default).",
    )
    parser.add_argument(
        "--fingerprint",
        default=None,
        help="Show only this config_fingerprint's recorded negative results.",
    )
    args = parser.parse_args(argv)

    tracking_root = args.tracking_root or str(lake_paths.mlflow_tracking_root())
    rows = query_negative_results(
        tracking_root=tracking_root, config_fingerprint=args.fingerprint
    )
    for fingerprint, reason, when, run_id, code_hash, data_hash in rows:
        print(
            f"{fingerprint}\treason={reason!r}\twhen={when}\trun_id={run_id}\t"
            f"code_hash={code_hash}\tdata_hash={data_hash}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
