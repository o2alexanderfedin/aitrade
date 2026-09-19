"""D-04-02: one feature code path, proven by three deliberately different
ways of calling it producing the same bytes.

THE TEST THAT MATTERS IS `test_three_call_sites_are_byte_identical`, and
what makes it mean anything is
`test_the_three_call_sites_drive_the_kernel_differently`. Three wrappers
around one identical call would pass the first and prove nothing; the
second one pins that training enters the kernel ONCE, inference once per
chunk, and the simulator once per row. Byte-equality between three shapes
that are genuinely different is evidence; byte-equality between three
spellings of the same call is a tautology.

Normalization is OFF in the equality tests, deliberately. Within the
training segment the transform is the EXPANDING z-score and outside it the
FROZEN one (D-04-06), so training's normalized columns are supposed to
differ from inference's on the same rows. The FEAT-01 claim is about the
four raw feature columns; the normalized columns get their own equality
test between the two call sites that share one loaded artifact.
"""

from __future__ import annotations

import io

import numpy as np
import polars as pl
import pytest

from data.time_ns import NS_PER_SECOND, TRADE_FLOW_WINDOW_NS
from features import api
from features.api import (
    FEATURE_PASS_SCHEMA,
    LabelsNotHereError,
    NormalizationRequiredError,
    Z_SUFFIX,
    chunk_events,
    compute_decision_rows,
    decision_rows_to_frame,
    event_row_stream,
    for_inference,
    for_simulation,
    for_training,
)
from features.event_stream import decision_row_index
from features.normalize import fit_normalization
from features.tier import FEATURE_COLUMNS
from tests.fixtures.event_streams import QTY_SCALE_F, build_events, quote, trade

MILLISECOND_NS = NS_PER_SECOND // 1_000


def interleaved_stream(n: int = 240, *, seed: int = 11) -> dict[str, np.ndarray]:
    """A merged stream whose `etime` groups hold one to three rows, with
    quotes and trades on both sides of every plausible chunk boundary.

    Both properties are load-bearing and are asserted by
    `test_the_fixture_exercises_the_cases_that_can_break`: a stream of
    singleton groups cannot catch a boundary inside a group, and a stream
    without trades within one second of each other cannot catch a ring
    buffer reset at a chunk boundary.
    """
    rng = np.random.default_rng(seed)
    rows = []
    etime = 0
    bid = 100.0
    for _ in range(n):
        if rng.random() < 0.55:
            etime += int(rng.integers(1, 30)) * MILLISECOND_NS
        if rng.random() < 0.45:
            rows.append(
                trade(
                    etime,
                    bid + 0.05,
                    int(rng.integers(1, 500_000_000)) / QTY_SCALE_F,
                    int(rng.choice([-1, 0, 1])),
                )
            )
        else:
            bid = 100.0 + int(rng.integers(-30, 30)) * 0.1
            rows.append(
                quote(
                    etime,
                    bid,
                    int(rng.integers(1, 1_000_000)) / QTY_SCALE_F,
                    bid + 0.1 * int(rng.integers(1, 4)),
                    int(rng.integers(1, 1_000_000)) / QTY_SCALE_F,
                )
            )
    return build_events(rows)


def _parquet_bytes(frame: pl.DataFrame) -> bytes:
    buffer = io.BytesIO()
    frame.write_parquet(buffer, compression="uncompressed")
    return buffer.getvalue()


def _assert_frames_byte_identical(left: pl.DataFrame, right: pl.DataFrame, what: str):
    assert left.schema == right.schema, f"{what}: schema differs"
    assert left.height == right.height, f"{what}: {left.height} rows vs {right.height}"
    for name in left.columns:
        assert np.array_equal(
            left[name].to_numpy(), right[name].to_numpy(), equal_nan=True
        ), f"{what}: column {name!r} differs"
    assert _parquet_bytes(left) == _parquet_bytes(right), (
        f"{what}: columns compare equal but the serialized frames do not"
    )


# ------------------------------------------------------------ the fixture


def test_the_fixture_exercises_the_cases_that_can_break():
    events = interleaved_stream()
    etime = events["etime"]
    distinct = np.unique(etime).size
    assert distinct < etime.size, "every etime is unique -- no group to split"

    for size in (7, 64):
        boundaries = [
            index
            for index in range(size, etime.size, size)
            if etime[index] == etime[index - 1]
        ]
        assert boundaries, f"no chunk boundary of size {size} lands inside a group"

    trades = etime[events["trade_side"] != 0]
    within_window = np.diff(trades) <= TRADE_FLOW_WINDOW_NS
    assert within_window.sum() > 10, (
        "too few trades within one second of each other -- a ring reset at a "
        "chunk boundary would not show up in trade_flow"
    )

    frame, _state = compute_decision_rows(events)
    for name in FEATURE_COLUMNS:
        column = frame[name].to_numpy()
        finite = column[np.isfinite(column)]
        assert finite.size > 10 and finite.std() > 0.0, (
            f"{name} is constant on this fixture -- an equality test over it "
            "would pass on a function returning a constant"
        )


# --------------------------------------------------- the load-bearing test


def test_three_call_sites_are_byte_identical():
    events = interleaved_stream()

    training, _state, _params = for_training(events)
    inference, _istate, _iparams = for_inference(chunk_events(events, 64))
    simulation = decision_rows_to_frame(for_simulation(event_row_stream(events)))

    _assert_frames_byte_identical(training, inference, "training vs inference")
    _assert_frames_byte_identical(training, simulation, "training vs simulation")
    assert training.schema == dict(FEATURE_PASS_SCHEMA)
    assert training.height == decision_row_index(events["etime"]).size


@pytest.mark.parametrize("size", [1, 2, 7, 1000])
def test_chunk_boundaries_do_not_matter(size):
    events = interleaved_stream()
    training, _state, _params = for_training(events)
    chunked, _state2, _params2 = for_inference(chunk_events(events, size))
    _assert_frames_byte_identical(training, chunked, f"chunk size {size}")


def test_a_chunk_boundary_inside_an_etime_group_still_emits_one_row():
    """The case a naive implementation gets wrong: a decision row is the
    LAST row of its `etime` group, so a chunk that ends mid-group must not
    emit its own last row as a decision."""
    events = interleaved_stream()
    etime = events["etime"]
    cut = next(i for i in range(1, etime.size) if etime[i] == etime[i - 1])

    training, _state, _params = for_training(events)
    split, _state2, _params2 = for_inference(
        [
            {name: column[:cut] for name, column in events.items()},
            {name: column[cut:] for name, column in events.items()},
        ]
    )
    _assert_frames_byte_identical(training, split, "boundary inside a group")


def test_the_three_call_sites_drive_the_kernel_differently(monkeypatch):
    """Byte-equality is only evidence if the three are not the same call.

    Exact counts, not "they differ": at chunk size 1 inference and the
    simulator enter the kernel the same number of times, so "all three
    differ" would be false for a legitimate implementation while a
    `for_simulation` that secretly called `for_training` would still be
    caught here.
    """
    events = interleaved_stream()
    rows = events["etime"].size
    calls = []

    real = api.run_kernel_checked

    def counting(events_arg, **kwargs):
        calls.append(events_arg["etime"].shape[0])
        return real(events_arg, **kwargs)

    monkeypatch.setattr(api, "run_kernel_checked", counting)

    calls.clear()
    for_training(events)
    assert len(calls) == 1 and calls[0] == rows, (
        f"training entered the kernel {len(calls)} time(s) with {calls[:3]} "
        "rows -- it is one batch over the whole stream"
    )

    calls.clear()
    chunks = list(chunk_events(events, 64))
    for_inference(chunks)
    assert len(calls) == len(chunks)
    assert sum(calls) == rows

    calls.clear()
    list(for_simulation(event_row_stream(events)))
    assert len(calls) == rows, (
        f"the simulator entered the kernel {len(calls)} time(s) for {rows} "
        "rows -- it is not driving it row by row"
    )
    assert set(calls) == {1}


def test_simulation_emits_a_group_only_once_a_larger_etime_arrives():
    """The emission rule, observed rather than assumed.

    Per-row streaming cannot know a row is the last of its `etime` until
    the next row arrives, so the decision row for `etime = t` is emitted
    when the first row with `etime > t` is CONSUMED -- one group late, by
    construction. Driving the generator to exhaustion and checking the
    first and last rows would prove none of that (a batch pass satisfies
    it too), so the source counts what the simulator has pulled and the
    assertions are about WHEN each decision row appears.
    """
    events = interleaved_stream(n=60, seed=3)
    rows = list(event_row_stream(events))
    etime = events["etime"]
    consumed: list[int] = []

    def counting_source():
        for index, row in enumerate(rows):
            consumed.append(index)
            yield row

    stream = for_simulation(counting_source())
    first_group = int(etime[0])
    opener = int(np.flatnonzero(etime > first_group)[0])

    first = next(stream)
    assert first["etime"] == first_group
    assert len(consumed) == opener + 1, (
        f"the decision row for the first etime group appeared after "
        f"{len(consumed)} source rows; the rule says it appears on row "
        f"{opener} -- the first row with a larger etime"
    )

    seen = [first]
    consumed_when_yielded = []
    for row in stream:
        consumed_when_yielded.append(len(consumed))
        seen.append(row)

    assert consumed_when_yielded[-1] == len(rows), (
        "the final etime group was emitted before the source was exhausted "
        "-- nothing can know it is final until then"
    )
    assert seen[-1]["etime"] == int(etime[-1])
    assert len(seen) == int(np.unique(etime).size), (
        "one decision row per distinct etime, no more and no fewer"
    )


def test_an_empty_stream_is_an_empty_frame_not_a_crash():
    """A serving loop with nothing in its window, and a simulator that
    yielded nothing -- both are ordinary, and both must produce the same
    empty frame the non-empty path produces."""
    empty, _state, _params = for_inference([])
    assert empty.height == 0 and empty.schema == dict(FEATURE_PASS_SCHEMA)

    events = interleaved_stream(n=20)
    blank = {name: column[:0] for name, column in events.items()}
    all_blank, _s, _p = for_inference([blank, blank])
    assert all_blank.height == 0 and all_blank.schema == dict(FEATURE_PASS_SCHEMA)

    assert decision_rows_to_frame([]).schema == dict(FEATURE_PASS_SCHEMA)
    assert decision_rows_to_frame(for_simulation(iter([]))).height == 0

    batch, _s2 = compute_decision_rows(blank)
    _assert_frames_byte_identical(batch, all_blank, "empty batch vs empty chunks")


# --------------------------------------------------------- normalization


def test_inference_and_simulation_require_a_normalization_artifact():
    events = interleaved_stream()
    with pytest.raises(NormalizationRequiredError, match="for_training"):
        for_inference(chunk_events(events, 64), normalize=True)
    with pytest.raises(NormalizationRequiredError, match="for_training"):
        list(for_simulation(event_row_stream(events), normalize=True))

    # and neither of them has a way to ASK for a fit
    with pytest.raises(TypeError):
        for_inference(chunk_events(events, 64), fit_normalization=True)
    with pytest.raises(TypeError):
        list(for_simulation(event_row_stream(events), fit_normalization=True))


def test_only_training_fits_and_it_fits_on_its_own_rows():
    events = interleaved_stream()
    frame, _state, params = for_training(events, fit_normalization=True)

    assert params is not None
    for name in FEATURE_COLUMNS:
        assert f"{name}{Z_SUFFIX}" in frame.columns
        column = frame[name].to_numpy()
        finite = column[np.isfinite(column)]
        assert params[name] == fit_normalization({name: column})[name]
        assert params[name][0] == finite.size

    with pytest.raises(ValueError, match="both"):
        for_training(events, fit_normalization=True, normalization=params)


def test_inference_and_simulation_apply_the_same_frozen_parameters():
    events = interleaved_stream()
    _frame, _state, params = for_training(events, fit_normalization=True)

    inference, _s, _p = for_inference(chunk_events(events, 64), normalization=params)
    simulation = decision_rows_to_frame(
        for_simulation(event_row_stream(events), normalization=params)
    )
    _assert_frames_byte_identical(inference, simulation, "normalized inference vs sim")

    z_columns = [f"{name}{Z_SUFFIX}" for name in FEATURE_COLUMNS]
    assert all(name in inference.columns for name in z_columns)
    for name in FEATURE_COLUMNS:
        count, mean, m2 = params[name]
        raw = inference[name].to_numpy()
        std = (m2 / (count - 1)) ** 0.5
        assert np.array_equal(
            inference[f"{name}{Z_SUFFIX}"].to_numpy(),
            (raw - mean) / std,
            equal_nan=True,
        ), f"{name}: the frozen transform is not (x - mean) / std from the artifact"


# ---------------------------------------------------------------- labels


def test_labels_are_refused_here_with_the_reason():
    events = interleaved_stream()
    with pytest.raises(LabelsNotHereError, match="build_features_day"):
        compute_decision_rows(events, labels=True)
    with pytest.raises(LabelsNotHereError):
        for_training(events, labels=True)
