"""The plumbing a `models.regression` fit needs, at `tmp_path` scale: a
cache Parquet, a `features_norm` artifact resolvable by manifest id, and the
`FitInputs` that names them.

WHY THIS LIVES IN `tests/fixtures/` RATHER THAN IN A TEST FILE.
`tests/models/` has no `__init__.py` on purpose (a same-named test package
shadows `mvp/models/`), and under `--import-mode=importlib` two test modules
in that directory cannot import each other. Three test files in plan 07-06
need the same artifact-and-cache pair, so the alternative to this module is
the same forty lines copied three times -- and `tests/fixtures/` is already
where this project puts shared rigs (`model_span.py`, `harness_span.py`,
`prediction_span.py`).

WHY IT DOES NOT GO THROUGH THE ACCESSOR, AND WHAT IT THEREFORE IS NOT.
`harness.accessor.materialize` is what COUNTS a validation look (D-05-11),
and pre-commit hook 19 runs the full suite on every commit -- so a test that
reached a canonical root would spend an irreversible look once per commit
forever (D-07-34). The fits these helpers serve are about the ESTIMATOR
layer: coefficient order, the price-column refusals, agreement with an
independent oracle. None of them needs a segment manifest, a fold geometry
or an admission policy, and `tests/fixtures/model_span.py` remains the rig
for the tests that do. This module writes a plain Parquet with exactly the
four columns a fit reads, which is precisely what the D-07-05 cache is.

`features.tier.load_features` is never imported here -- D-05-15's rule, with
`tools/check_harness_accessor_only.py` as its tripwire.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import polars as pl

from features.normalize import (
    fit_normalization,
    normalization_dataset,
    write_normalization_artifact,
)
from models.protocol import FitInputs
from models.regression import (
    FEATURE_NAMES,
    ROW_MASK_COLUMN,
    TARGET_NAME,
    FitContext,
)

SYMBOL: str = "BTCUSDT"
CODE_HASH: str = "0" * 40
TRAIN_END_DATE: str = "2026-09-13"

#: The two days the fake source feature manifests cover, newest last.
#: `features.normalize._train_dates` derives the holdout gate's dates from
#: these, and FAILS CLOSED when an input names none -- so the fixture has to
#: name them.
SOURCE_DATES: tuple[str, ...] = ("2026-09-12", "2026-09-13")

#: How strongly each model input carries the label before noise, and the
#: noise multiplier `s` of `tests/fixtures/model_span.py`'s
#: `R^2 = A / (A + s^2)` law (`A = sum(a^2)`). Same numbers as that rig, so a
#: fit on this cache lands in the same measured 0.013..0.048 band the real
#: train-internal splits showed -- a learnable frame, not a solved one.
SIGNAL_COEFFICIENTS: dict[str, float] = {
    "imb_top": 1.0,
    "ofi": 0.5,
    "trade_flow": 0.3,
}
NOISE_SCALE: float = 6.0


def make_context(tmp_path: Path) -> FitContext:
    """The trainers' constructor state pointed at THIS test's `tmp_path`:
    the same `lake`/`registry` pair every helper here writes to, and a
    fixture code hash.

    There is no canonical-root fallback in `FitContext` on purpose, so this
    is the only way a test can get a trainer at all -- which is the property
    that keeps the suite off the real registry under hooks 18/19.
    """
    return FitContext(
        symbol=SYMBOL,
        lake_root=Path(tmp_path) / "lake",
        registry_root=Path(tmp_path) / "registry",
        code_hash=CODE_HASH,
    )


def write_source_feature_manifests(registry_root: Path) -> list[dict]:
    """Two `inputs[]` entries standing in for the feature manifests a real
    normalisation fit consumes -- written to disk so their sha256 is a real
    file hash and `_train_dates` can resolve them.

    Copied from `tests/features/test_normalize.py:_artifact_inputs` rather
    than imported: `tests/features/` is not a package either, and the shape
    is the point, not the code.
    """
    registry_root = Path(registry_root)
    inputs: list[dict] = []
    for index, manifest_id in enumerate(("a" * 64, "b" * 64)):
        path = (
            registry_root / "manifests" / f"{SYMBOL}.features" / f"{manifest_id}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "manifest_id": manifest_id,
            "row_count": 10 * (index + 1),
            "partitions": [{"date": SOURCE_DATES[index]}],
        }
        path.write_text(json.dumps(body))
        inputs.append(
            {
                "path": str(path.resolve()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "rows": body["row_count"],
                "dataset": f"{SYMBOL}.features",
                "manifest_id": manifest_id,
                "role": "features_day",
            }
        )
    return inputs


def write_normalization(
    params: dict[str, tuple[int, float, float]],
    *,
    lake_root: Path,
    registry_root: Path,
    train_row_count: int | None = None,
    train_etime_range: tuple[int, int] = (0, 1),
) -> str:
    """Write a real `features_norm` artifact through the real writer and
    return its manifest id, ready for `load_normalization`."""
    if train_row_count is None:
        train_row_count = max(count for count, _mean, _m2 in params.values())
    manifest = write_normalization_artifact(
        params,
        symbol=SYMBOL,
        train_end_date=TRAIN_END_DATE,
        source_feature_manifest_ids=write_source_feature_manifests(registry_root),
        train_row_count=train_row_count,
        train_etime_range=train_etime_range,
        lake_root=Path(lake_root),
        registry_root=Path(registry_root),
        code_hash=CODE_HASH,
    )
    assert manifest["dataset"] == normalization_dataset(SYMBOL)
    return manifest["manifest_id"]


def identity_params(
    names: tuple[str, ...], *, rows: int
) -> dict[str, tuple[int, float, float]]:
    """Welford `(count, mean, M2)` triples that make `apply_normalization`
    the IDENTITY: mean 0, and `M2 = rows - 1` so that
    `welford_std = sqrt(M2 / (count - 1))` is exactly 1.0.

    What it buys: with the transform an identity, a fit's design matrix IS
    the values written into the cache, so a coefficient can be checked
    against a CONSTRUCTED truth (`y = 3 + 2*x0 - x1 + 0.5*x2`) instead of
    against a pinned literal no BLAS agrees on (correction C6). It is a
    fixture device, not a claim about real parameters.
    """
    return {name: (int(rows), 0.0, float(rows - 1)) for name in names}


def measured_params(
    values: dict[str, np.ndarray],
) -> dict[str, tuple[int, float, float]]:
    """The real `fit_normalization` over `values` -- the shape a real run's
    artifact has, including a `mid` row when one is passed in."""
    return fit_normalization(values)


def learnable_columns(
    rows: int, *, seed: int = 20260925, noise_scale: float = NOISE_SCALE
) -> dict[str, np.ndarray]:
    """Three features and one target with `model_span.py`'s correlation
    structure: `x_i = a_i * z + s * e_i` over a unit-variance label `z`.

    The target is scaled to the real 10-second return's order of magnitude
    (1e-4), because ElasticNet's `alpha * l1_ratio` is a soft threshold in
    per-sample gradient units -- on a target of the wrong scale the grid's
    larger alphas would look harmless when in reality they zero every
    coefficient.
    """
    rng = np.random.default_rng(seed)
    z = rng.standard_normal(rows)
    z = (z - z.mean()) / z.std()
    noise = rng.standard_normal((len(SIGNAL_COEFFICIENTS), rows))
    columns = {
        name: coefficient * z + noise_scale * noise[index]
        for index, (name, coefficient) in enumerate(SIGNAL_COEFFICIENTS.items())
    }
    columns[TARGET_NAME] = 1e-4 * z
    return columns


def write_cache(
    path: Path, columns: dict[str, np.ndarray], *, nan_to_null: bool = False
) -> Path:
    """One D-07-05-shaped cache Parquet: Float64 columns, in the order given.

    `nan_to_null=True` stores NaN as a real polars NULL, which is how the
    tier's own frames carry a missing label -- and the case
    `models.regression._read_columns` accounts for explicitly, because a
    nullable column becomes a NaN-filled COPY at `.to_numpy()` without
    raising.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        {
            name: pl.Series(name, np.asarray(values, dtype=np.float64), pl.Float64)
            for name, values in columns.items()
        }
    )
    if nan_to_null:
        frame = frame.with_columns(
            [pl.col(name).fill_nan(None) for name in frame.columns]
        )
    frame.write_parquet(path)
    return path


def write_row_mask(path: Path, keep: np.ndarray) -> Path:
    """The single-Boolean-column Parquet `FitInputs.row_mask_path` names."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {ROW_MASK_COLUMN: pl.Series(ROW_MASK_COLUMN, np.asarray(keep, dtype=bool))}
    ).write_parquet(path)
    return path


def make_fit_inputs(
    cache_path: Path,
    normalization_manifest_id: str,
    *,
    feature_names: tuple[str, ...] = FEATURE_NAMES,
    target_name: str = TARGET_NAME,
    row_mask_path: Path | None = None,
    seed: int = 20260925,
) -> FitInputs:
    return FitInputs(
        cache_path=Path(cache_path),
        feature_names=feature_names,
        target_name=target_name,
        row_mask_path=row_mask_path,
        normalization_manifest_id=normalization_manifest_id,
        seed=seed,
    )


def learnable_fit_inputs(
    tmp_path: Path,
    *,
    rows: int = 4_000,
    seed: int = 20260925,
    include_mid_in_artifact: bool = False,
    nan_rows: int = 0,
) -> tuple[FitInputs, dict[str, np.ndarray]]:
    """A cache plus a MEASURED artifact, the pair most tests want, and the
    raw columns so a test can assert against them.

    `include_mid_in_artifact` fits the artifact over a fourth `mid` column
    too -- the real artifact's shape, and research assumption A5's test case:
    "it came from the normalisation artifact" must not be a sufficient filter
    for a column reaching an estimator.

    `nan_rows` plants NaN in the first `nan_rows` rows of the target and of
    `imb_top` (both, so the mask has to be an AND across columns) and stores
    them as polars nulls.
    """
    columns = learnable_columns(rows, seed=seed)
    artifact_values = {name: columns[name] for name in FEATURE_NAMES}
    if include_mid_in_artifact:
        artifact_values["mid"] = 70_000.0 + np.arange(rows, dtype=np.float64) * 0.1
    if nan_rows:
        columns = {name: values.copy() for name, values in columns.items()}
        columns[TARGET_NAME][:nan_rows] = np.nan
        columns[FEATURE_NAMES[0]][:nan_rows] = np.nan
    manifest_id = write_normalization(
        measured_params(artifact_values),
        lake_root=tmp_path / "lake",
        registry_root=tmp_path / "registry",
        train_row_count=rows,
    )
    cache_path = write_cache(
        tmp_path / "cache" / "train.parquet", columns, nan_to_null=bool(nan_rows)
    )
    return make_fit_inputs(cache_path, manifest_id, seed=seed), columns
