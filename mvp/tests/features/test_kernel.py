"""The `@njit` kernel is the same computation as the readable one, and the
same computation whether it is called once or a row at a time.

Two claims carry the whole module, and both are asserted BITWISE
(`np.array_equal(..., equal_nan=True)`, never `allclose`): "close enough"
is exactly how a silent drift survives, and a drift here is a drift in
every number this project will ever report.

1. EQUIVALENCE -- kernel output equals `features.reference` output on every
   shape Plan 03 pins plus hypothesis-generated streams. The reference is a
   deliberately different implementation (deque, Python int), so agreement
   is evidence rather than a tautology.
2. RESUMABILITY -- one call over a whole stream and N calls over its chunks
   produce byte-identical OUTPUT. The training path calls the kernel once
   over a day and the simulator calls it row by row; if the state contract
   were incomplete those two would silently disagree, and nothing else in
   the project would notice.
"""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from data.time_ns import TRADE_FLOW_WINDOW_NS
from features.kernel import (
    RING_CAPACITY,
    STATE_F64_SLOTS,
    STATE_I64_SLOTS,
    KernelStatusError,
    new_state,
    run_kernel,
    run_kernel_checked,
)
from features.reference import (
    STATUS_ARRAY_LENGTH_MISMATCH,
    STATUS_BOTH_TOP_SIZES_ZERO,
    STATUS_OK,
    STATUS_RING_NOT_POWER_OF_TWO,
    STATUS_RING_OVERFLOW,
    FEATURE_OUTPUT_NAMES,
    new_outputs,
    run_reference_checked,
)
from tests.fixtures.event_streams import (
    build_events,
    canonical_streams,
    quote,
    random_events,
    trade,
)

SECOND = TRADE_FLOW_WINDOW_NS
INPUT_NAMES = (
    "etime",
    "source_rank",
    "bid_price",
    "bid_qty",
    "ask_price",
    "ask_qty",
    "trade_qty",
    "trade_side",
)
STREAMS = canonical_streams()


def _same(a, b, name):
    if name == "warmup":
        return np.array_equal(a, b)
    return np.array_equal(a, b, equal_nan=True)


def _raw_call(events, out, state, start, stop):
    """The raw `@njit` entry point over `events[start:stop]`, writing into
    the matching slice of `out` and carrying `state`."""
    state_i64, state_f64, ring_t, ring_v = state
    return run_kernel(
        *(events[name][start:stop] for name in INPUT_NAMES),
        *(
            out[name][start:stop]
            for name in ("mid", "imb_top", "ofi", "trade_flow", "warmup")
        ),
        ring_t,
        ring_v,
        state_i64,
        state_f64,
    )


# --------------------------------------------------------------------------
# 1. Equivalence
# --------------------------------------------------------------------------


@pytest.mark.parametrize("stream", sorted(STREAMS))
def test_kernel_matches_the_reference_bitwise(stream):
    events = STREAMS[stream]
    expected = run_reference_checked(events)
    actual = run_kernel_checked(events)
    for name in FEATURE_OUTPUT_NAMES:
        assert _same(expected[name], actual[name], name), (
            f"{stream}: {name} differs between the kernel and the reference"
        )


def test_the_equivalence_fixtures_cover_every_pinned_shape():
    """A guard on the guard: the equivalence test is parametrized over
    whatever `canonical_streams()` holds, so a shape silently dropped from
    there would silently stop being checked."""
    required = {
        "ofi_repeated_identical_quote",
        "ofi_bid_price_improving",
        "ofi_bid_resized",
        "ofi_bid_swept",
        "ofi_ask_price_improving",
        "ofi_ask_resized",
        "ofi_ask_swept",
        "ofi_first_quote_is_nan",
        "window_open_end_excluded",
        "window_one_ns_inside",
        "unknown_side_contributes_zero",
        "empty_window_after_a_gap",
        "flow_exactly_cancelled",
        "trade_only_before_the_first_quote",
        "locked_then_crossed_then_normal",
        "warmup_boundary",
        "etime_ties_in_both_streams",
        "single_row",
    }
    assert required <= set(STREAMS)


@settings(deadline=None, max_examples=25, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_kernel_matches_the_reference_on_generated_streams(seed):
    events = random_events(np.random.default_rng(seed), n=200)
    expected = run_reference_checked(events)
    actual = run_kernel_checked(events)
    for name in FEATURE_OUTPUT_NAMES:
        assert _same(expected[name], actual[name], name), name


# --------------------------------------------------------------------------
# 2. Resumability
# --------------------------------------------------------------------------


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 64])
def test_batch_and_chunked_resume_are_bit_identical(chunk):
    """Assert on the OUTPUT, not on the state arrays.

    Several `state_i64` slots -- `empty_window_rows`, `crossed_locked_rows`,
    `max_occupancy` -- are CUMULATIVE counters that are correct precisely
    because they survive a resume, so comparing state arrays between a
    batch run and a chunked run proves something weaker than it looks: two
    runs could agree on every counter and still emit different values. The
    counters are therefore asserted separately below, and each assertion
    says what it covers.
    """
    events = _resume_stream()
    n = events["etime"].shape[0]

    whole_out = new_outputs(n)
    whole_state = new_state()
    assert _raw_call(events, whole_out, whole_state, 0, n) == STATUS_OK

    chunked_out = new_outputs(n)
    chunked_state = new_state()
    for start in range(0, n, chunk):
        status = _raw_call(
            events, chunked_out, chunked_state, start, min(start + chunk, n)
        )
        assert status == STATUS_OK

    # (i) covers every emitted value: the four features and the warmup flag
    for name in FEATURE_OUTPUT_NAMES:
        assert _same(whole_out[name], chunked_out[name], name), (
            f"{name} differs between one call and {chunk}-row calls"
        )

    # (ii) covers the cumulative counters specifically -- the slots that are
    # right only if they are carried, and that an output comparison cannot
    # see because they are never emitted into a column
    for slot in ("empty_window_rows", "crossed_locked_rows", "max_occupancy"):
        index = STATE_I64_SLOTS[slot]
        assert whole_state[0][index] == chunked_state[0][index], (
            f"cumulative counter {slot} did not survive a resume"
        )
        assert whole_state[0][index] > 0, (
            f"{slot} is 0 on this fixture -- the assertion above is vacuous"
        )

    # (iii) covers the carried quote state, which is what makes ofi and mid
    # correct across a chunk boundary rather than merely non-NaN
    assert np.array_equal(whole_state[1], chunked_state[1], equal_nan=True)


def test_the_chunk_boundary_actually_falls_mid_window():
    """A resume test whose chunks are all longer than the stream would pass
    on a kernel with no state at all."""
    events = _resume_stream()
    span = int(events["etime"][-1] - events["etime"][0])
    assert span > TRADE_FLOW_WINDOW_NS, (
        "the fixture must span more than one window, or a chunk boundary "
        "never separates a trade from the row that must still see it"
    )


def test_a_single_row_at_a_time_is_the_simulator_call_shape():
    """D-04-02's single code path, stated as the property that makes it
    mean something: the simulator's row-by-row call and the trainer's
    one-shot call are the same computation."""
    events = STREAMS["etime_ties_in_both_streams"]
    n = events["etime"].shape[0]
    batch = run_kernel_checked(events)

    out = new_outputs(n)
    state = new_state()
    for i in range(n):
        assert _raw_call(events, out, state, i, i + 1) == STATUS_OK
    for name in FEATURE_OUTPUT_NAMES:
        assert _same(batch[name], out[name], name), name


# --------------------------------------------------------------------------
# 3. Status codes, never a silent wrap and never a raise from inside @njit
# --------------------------------------------------------------------------


def _resume_stream():
    """The fixture the resume test needs: long enough to span several
    windows, with a GAP that empties the window and a LOCKED quote, so that
    all three cumulative counters are non-zero and the assertions on them
    are not vacuous."""
    rng = np.random.default_rng(7)
    rows = []

    def segment(base, count):
        for _ in range(count):
            etime = base + int(rng.integers(0, 3 * SECOND))
            if rng.random() < 0.45:
                rows.append(
                    trade(
                        etime,
                        100.5,
                        int(rng.integers(1, 500_000_000)) / 1e8,
                        int(rng.choice([-1, 0, 1])),
                    )
                )
            else:
                bid = 100.0 + int(rng.integers(-20, 20)) * 0.1
                rows.append(
                    quote(
                        etime,
                        bid,
                        int(rng.integers(1, 1_000_000)) / 1e8,
                        bid + 0.1 * int(rng.integers(1, 4)),
                        int(rng.integers(1, 1_000_000)) / 1e8,
                    )
                )

    segment(0, 150)
    rows.append(quote(4 * SECOND, 101.0, 0.5, 101.0, 0.7))  # locked
    segment(20 * SECOND, 150)  # after a 16 s gap: the window is empty again
    return build_events(rows)


def _nine_trades_in_one_window():
    rows = [quote(0, 100.0, 5.0, 101.0, 7.0)]
    rows += [trade(1 + i, 100.5, 0.5, 1) for i in range(9)]
    return build_events(rows)


def test_ring_overflow_returns_a_status_not_a_wrap():
    """T-04-12. A ring that wraps silently is a feature that looks fine and
    is wrong, so the guard returns a status and the offending row index and
    the wrapper turns that into an exception naming the row."""
    events = _nine_trades_in_one_window()
    n = events["etime"].shape[0]
    out = new_outputs(n)
    state = new_state(capacity=8)

    status = _raw_call(events, out, state, 0, n)
    assert status == STATUS_RING_OVERFLOW
    offending = int(state[0][STATE_I64_SLOTS["error_row"]])
    assert offending == 8, (
        "a cap-8 ring holds 7 entries (a full ring is indistinguishable "
        "from an empty one), so the 8th trade -- row 8 -- is the offender"
    )

    with pytest.raises(KernelStatusError) as excinfo:
        run_kernel_checked(events, state=new_state(capacity=8))
    message = str(excinfo.value)
    assert "ring buffer is full" in message
    assert "at row 8" in message
    assert "trade_qty=0.5" in message


def test_the_real_ring_capacity_does_not_overflow_on_the_same_stream():
    """The default capacity is not merely large, it is large for a measured
    reason: 1<<16 against a measured maximum real occupancy of 5,092."""
    events = _nine_trades_in_one_window()
    run_kernel_checked(events)
    assert RING_CAPACITY == 1 << 16
    assert RING_CAPACITY > 12 * 5092


def test_both_top_sizes_zero_returns_the_assertion_status():
    """D-04-16's assertion, reported as a STATUS. A `raise` inside an
    `@njit` function costs the compile-time specialisation and gives back
    only a message; a status plus a row index is strictly more."""
    events = build_events([quote(0, 100.0, 0.0, 101.0, 0.0)])
    out = new_outputs(1)
    state = new_state()
    assert _raw_call(events, out, state, 0, 1) == STATUS_BOTH_TOP_SIZES_ZERO
    assert int(state[0][STATE_I64_SLOTS["error_row"]]) == 0

    with pytest.raises(KernelStatusError, match="imb_top is undefined"):
        run_kernel_checked(events)


def test_a_qty_the_fixed_point_cannot_represent_returns_a_status():
    events = build_events(
        [quote(0, 100.0, 5.0, 101.0, 7.0), trade(1, 100.5, 0.123456789, 1)]
    )
    with pytest.raises(KernelStatusError, match="not integral at 1e-8"):
        run_kernel_checked(events)


def test_a_ring_that_is_not_a_power_of_two_is_refused():
    """The kernel indexes with `& (cap - 1)`, which is a modulo only for
    powers of two. A cap of 10 would wrap at 8 and corrupt the window
    silently -- so it is refused before the first row."""
    events = STREAMS["warmup_boundary"]
    n = events["etime"].shape[0]
    out = new_outputs(n)
    state = (
        np.zeros(len(STATE_I64_SLOTS), dtype=np.int64),
        np.full(len(STATE_F64_SLOTS), np.nan),
        np.empty(10, dtype=np.int64),
        np.empty(10, dtype=np.int64),
    )
    assert _raw_call(events, out, state, 0, n) == STATUS_RING_NOT_POWER_OF_TWO

    with pytest.raises(ValueError, match="power of two"):
        new_state(capacity=10)


def test_mismatched_array_lengths_are_refused_not_read_out_of_bounds():
    """numba does not bounds-check. An output array shorter than the input
    would be a silent out-of-bounds WRITE, so the lengths are checked
    before the loop."""
    events = STREAMS["warmup_boundary"]
    n = events["etime"].shape[0]
    out = new_outputs(n - 1)
    state = new_state()
    state_i64, state_f64, ring_t, ring_v = state
    status = run_kernel(
        *(events[name] for name in INPUT_NAMES),
        out["mid"],
        out["imb_top"],
        out["ofi"],
        out["trade_flow"],
        out["warmup"],
        ring_t,
        ring_v,
        state_i64,
        state_f64,
    )
    assert status == STATUS_ARRAY_LENGTH_MISMATCH


# --------------------------------------------------------------------------
# 4. Reported state
# --------------------------------------------------------------------------


def test_max_occupancy_is_reported():
    """Hand-computed: five trades land inside one 1 s window and the sixth
    arrives after the first three have aged out."""
    rows = [quote(0, 100.0, 5.0, 101.0, 7.0)]
    rows += [trade(i * SECOND // 10, 100.5, 0.25, 1) for i in range(1, 6)]
    rows += [trade(2 * SECOND, 100.5, 0.25, 1)]
    events = build_events(rows)

    state = new_state()
    run_kernel_checked(events, state=state)
    assert int(state[0][STATE_I64_SLOTS["max_occupancy"]]) == 5

    reference_state = None
    from features.reference import new_reference_state

    reference_state = new_reference_state()
    run_reference_checked(events, state=reference_state)
    assert reference_state.max_occupancy == 5, (
        "the two implementations must sample occupancy at the same point "
        "in the row -- after eviction, as the research sketch does"
    )


def test_the_state_slot_maps_are_the_documented_contract():
    """Later plans and the simulator index these by name. A renamed or
    reordered slot is a silent miscarry, so the contract is asserted."""
    assert list(STATE_I64_SLOTS) == [
        "head",
        "tail",
        "acc_scaled",
        "max_occupancy",
        "n_quotes_seen",
        "first_event_etime",
        "have_first_event",
        "error_row",
        "empty_window_rows",
        "crossed_locked_rows",
    ]
    assert list(STATE_F64_SLOTS) == [
        "prev_bid_price",
        "prev_bid_qty",
        "prev_ask_price",
        "prev_ask_qty",
        "last_ofi",
    ]
    assert sorted(STATE_I64_SLOTS.values()) == list(range(len(STATE_I64_SLOTS)))
    assert sorted(STATE_F64_SLOTS.values()) == list(range(len(STATE_F64_SLOTS)))

    state_i64, state_f64, ring_t, ring_v = new_state()
    assert state_i64.dtype == np.int64 and state_i64.shape == (len(STATE_I64_SLOTS),)
    assert state_f64.dtype == np.float64 and state_f64.shape == (len(STATE_F64_SLOTS),)
    assert ring_t.dtype == np.int64 and ring_v.dtype == np.int64
    assert ring_t.shape == ring_v.shape == (RING_CAPACITY,)
    assert int(state_i64[STATE_I64_SLOTS["error_row"]]) == -1
    assert all(math.isnan(x) for x in state_f64)


# --------------------------------------------------------------------------
# 5. numba discipline
# --------------------------------------------------------------------------


def test_kernel_reads_no_mutable_globals():
    """D-04-10 / the Phase 2 guardrail, run directly over this module so the
    failure names the line rather than the repo."""
    from tools.check_numba_globals import is_njit_decorated, scan_source

    import ast

    path = Path(__file__).resolve().parents[2] / "features" / "kernel.py"
    source = path.read_text()
    violations = scan_source(source, "features/kernel.py")
    assert violations == [], [str(v) for v in violations]

    tree = ast.parse(source)
    njit_functions = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and is_njit_decorated(node)
    ]
    assert "run_kernel" in njit_functions, (
        "a scan that finds no @njit function passes vacuously"
    )


def test_check_numba_globals_is_green_over_the_whole_package():
    result = subprocess.run(
        [sys.executable, "-m", "tools.check_numba_globals"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 njit functions" not in result.stdout, (
        "the guardrail must now be scanning a real kernel"
    )
