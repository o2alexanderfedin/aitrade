"""A predictions-tier partition written into a `tmp_path` lake through the
REAL `models.predictions.write_prediction_table` -- the same
builder-plus-real-writer shape `harness_span.py` and `model_span.py` use, one
tier over.

WHAT THE CALLER MUST HAVE ALREADY. A prediction table is addressed by
`(segment_manifest, segment, predictor)` (D-07-14), so this builder needs a
segment manifest that exists (`tests.fixtures.model_span.
build_model_span_fixture` issues one) and the frame that segment's accessor
look returned. It writes NOTHING to the real lake and issues no manifest
outside the `registry_root` it is handed.

THE EMISSION IS POSITIONAL, AND THAT IS THE POINT. The three columns are
taken straight off the scored frame -- no join, no sort, no group_by -- so
there is nothing in the construction that COULD misalign. Every test that
then asserts `assert_table_aligned` is asserting the write/read round trip
preserved an alignment the emission established, which is the only order in
which that claim means anything.

TWO PROPERTIES OF THE LAKE DIRECTORY THE TIER GATE INSISTS ON, so a fixture
that trips them is debugged here rather than in the loader:
`lake_root/predictions/` must be a REAL directory and not a symlink, and
every partition file must have `st_nlink == 1`. `data.store.
_enforce_tier_containment` refuses every read otherwise -- containment cannot
be decided by resolving paths through a link, and a hard link makes one set
of bytes reachable under two names. `tmp_path` satisfies both by default;
a fixture that copies a partition with `os.link` does not.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl

from models.predictions import (
    PREDICTION_TABLE_SCHEMA,
    predictions_dataset,
    write_prediction_table,
)

SYMBOL = "BTCUSDT"

#: A FABRICATED 64-hex `predictor_id`. Fabricated deliberately: a real one is
#: `models.predictor_id.compute_predictor_id`'s hash over a fitted recipe, and
#: nothing in the storage layer may depend on having fitted anything.
FIXTURE_PREDICTOR_ID: str = "5f" * 32

CODE_HASH = "deadbeef"


def prediction_table_from_frame(frame: pl.DataFrame, pred: np.ndarray) -> pl.DataFrame:
    """`{etime, decision_seq, pred}` taken POSITIONALLY off `frame`, with
    `pred` in RETURN units (D-07-33) at one value per row."""
    if pred.shape != (frame.height,):
        raise AssertionError(
            f"prediction_table_from_frame: pred has shape {pred.shape}, the "
            f"frame has {frame.height} rows"
        )
    return pl.DataFrame(
        {
            "etime": frame["etime"],
            "decision_seq": frame["decision_seq"],
            "pred": pl.Series("pred", np.asarray(pred, dtype=np.float64)),
        },
        schema=dict(PREDICTION_TABLE_SCHEMA),
    )


def build_prediction_span(
    lake_root: Path,
    registry_root: Path,
    *,
    frame: pl.DataFrame,
    segment_manifest_id: str,
    segment_name: str = "val",
    pred: np.ndarray | None = None,
    symbol: str = SYMBOL,
    predictor_id: str = FIXTURE_PREDICTOR_ID,
    inputs: list[dict] | None = None,
    code_hash: str = CODE_HASH,
) -> dict:
    """Write one prediction table for `frame` and return
    `{"manifest", "dataset", "table", "path"}`.

    `pred` defaults to a small, deterministic, row-index-derived return so a
    caller that only cares about storage does not have to invent one; it is
    never zero everywhere, because a table of zeros converts to exactly `mid`
    on every row and would make a downstream trade-count assertion vacuous.
    """
    if pred is None:
        pred = 1e-6 * np.sin(np.arange(frame.height, dtype=np.float64))
    table = prediction_table_from_frame(frame, pred)
    manifest = write_prediction_table(
        table,
        symbol=symbol,
        segment_manifest_id=segment_manifest_id,
        segment_name=segment_name,
        predictor_id=predictor_id,
        inputs=inputs,
        lake_root=lake_root,
        registry_root=registry_root,
        code_hash=code_hash,
    )
    return {
        "manifest": manifest,
        "dataset": predictions_dataset(symbol),
        "table": table,
        "path": Path(lake_root) / manifest["partitions"][0]["path"],
    }
