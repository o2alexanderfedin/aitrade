"""D-06-14: the simulator's output is bit-identical across two same-process
runs AND a fresh subprocess, proven by sha256 of the properly-sliced
(`[:fill_count]`-truncated) serialized outputs -- never by "the code ran
without error twice" (06-05-PLAN.md's own must_haves).

Written FIRST, per this file's `tdd="true"` task: before `_hash_result`
existed, every test below failed with `NameError: name '_hash_result' is
not defined` -- that RED is observed in this session's own tool transcript
(see 06-05-SUMMARY.md's "TDD Gate Compliance" section), not merely
asserted after the fact.

Precedent for the subprocess mechanism, reused in shape (not by import):
`tests/features/test_kernel.py:511-517`'s
`subprocess.run([sys.executable, "-m", ...], cwd=mvp_root, ...)` and
`tests/capture/test_ws_client_raw_archive.py`'s
`subprocess.run([sys.executable, "-c", script], cwd=pkg_root,
env={**os.environ, "PYTHONPATH": ...}, ...)` -- the latter is the one this
file's child-process test mirrors directly (a scripted `-c` child, not a
module).
"""

from __future__ import annotations

import hashlib
import inspect
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np

from sim.kernel import new_state, run_sim_checked
from sim.outputs import SimResult
from sim.ticks import PRICE_SCALE, TICK_SIZE_SCALED

MVP_ROOT = Path(__file__).resolve().parents[2]

#: Used only by this test module's own assertions (never referenced from
#: inside `_hash_result`'s body -- see that function's own docstring for
#: why it hardcodes the same five names locally instead).
_TRADE_LOG_COLUMNS = ("etime", "side", "price_ticks", "qty_scaled", "position_after")


def _price_at_ticks(ticks: int) -> float:
    """A raw price (the scale `pred` arrives in) at a given tick count --
    NOT `sim.ticks.price_to_ticks`'s inverse (that is for bid/ask, never
    for a model prediction; see `sim/kernel.py`'s module docstring).
    Mirrors `tests/sim/test_kernel.py`'s own local helper of the same
    name, duplicated here (not imported) because this function's source is
    also extracted verbatim into the subprocess child script below, and an
    inter-test-module import would break that extraction's
    self-containment.
    """
    return ticks * TICK_SIZE_SCALED / PRICE_SCALE


def _build_fixed_sequence(n: int = 500):
    """A DETERMINISTIC, non-random `n`-row decision sequence -- no RNG
    anywhere, on purpose: the same literal arithmetic, re-run in a fresh
    subprocess with no shared state, must produce the identical input
    array without depending on any cross-process RNG-algorithm-stability
    guarantee (numpy's default_rng makes none across versions; a fixed
    formula makes the question moot). `ask = bid + spread`, spread in
    [1, 5] ticks; `pred` is a raw price near the row's own mid, offset by
    a signed tick count in [-40, 40] -- mirrors
    `tests/sim/test_kernel.py`'s hypothesis strategy's own price-band
    reasoning (a bare tick-range integer would make every row trigger,
    which is the anti-vacuity mistake that strategy's docstring already
    warns against).
    """
    etimes: list[int] = []
    bids: list[int] = []
    asks: list[int] = []
    preds: list[float] = []
    t = 0
    for i in range(n):
        t += 100 + (i % 7)
        bid = 500_000 + ((i * 37) % 401) - 200
        spread = 1 + (i % 5)
        ask = bid + spread
        offset = ((i * 13) % 81) - 40
        pred_ticks_target = (bid + ask) // 2 + offset
        etimes.append(t)
        bids.append(bid)
        asks.append(ask)
        preds.append(_price_at_ticks(pred_ticks_target))
    return (
        np.array(etimes, dtype=np.int64),
        np.array(bids, dtype=np.int64),
        np.array(asks, dtype=np.int64),
        np.array(preds, dtype=np.float64),
    )


def _hash_arrays(arrays) -> str:
    """sha256 over `dtype.str + shape-as-bytes + arr.tobytes()` per array,
    in the order given, into ONE running hash -- 06-RESEARCH.md Q8's exact
    recommendation. NEVER a `repr()`/string form (float repr formatting is
    not guaranteed bit-stable the way raw bytes are)."""
    h = hashlib.sha256()
    for arr in arrays:
        h.update(arr.dtype.str.encode())
        h.update(np.array(arr.shape, dtype=np.int64).tobytes())
        h.update(arr.tobytes())
    return h.hexdigest()


def _hash_result(result: SimResult) -> str:
    """ONE FIXED declared order: the five trade-log columns SLICED to
    `[:fill_count]` (`sim/outputs.py`'s own uninitialised-tail hazard),
    then `equity_scaled` UNSLICED (one-per-decision-row, never has an
    uninitialised tail), then the three counters as one int64 array -- so
    this determinism check also catches a counters divergence, not only an
    array divergence (this file's `<absolute_rules>`: "Hash the serialized
    outputs (trade log, equity curve, counters)").

    The five-column-name tuple is hardcoded HERE rather than referencing
    the module-level `_TRADE_LOG_COLUMNS` constant, deliberately: this
    function's source is extracted verbatim via `inspect.getsource` into
    the subprocess child script below, and a reference to a module-level
    name the child script never defines would be a `NameError` waiting to
    happen in the child only, the one place this file's own suite cannot
    see it fail directly.
    """
    k = result.fill_count
    columns = ("etime", "side", "price_ticks", "qty_scaled", "position_after")
    arrays = [result.trade_log[name][:k] for name in columns]
    arrays.append(result.equity_scaled)
    arrays.append(
        np.array(
            [
                result.counters["trades"],
                result.counters["flips"],
                result.counters["rows_in_market"],
            ],
            dtype=np.int64,
        )
    )
    return _hash_arrays(arrays)


def _child_script() -> str:
    """A standalone `python3 -c` script computing the SAME hash as this
    module's own `_hash_result`, over the SAME fixed input as
    `_build_fixed_sequence` -- extracted via `inspect.getsource` (never
    hand-retyped) so the child process runs byte-identical logic to the
    parent by construction, with no risk of the two copies silently
    drifting apart. The child cannot import this test module directly
    (`mvp/tests/sim/` has no `__init__.py`, D-06-02, and a fresh `python3
    -c` is not a pytest collection anyway) -- this is the plan's own
    "duplicated inline in the spawned string, not imported" instruction,
    satisfied by extraction rather than by manual retyping.
    """
    funcs_src = "\n\n".join(
        textwrap.dedent(inspect.getsource(fn))
        for fn in (_price_at_ticks, _build_fixed_sequence, _hash_arrays, _hash_result)
    )
    return (
        "from __future__ import annotations\n"
        "import hashlib\n"
        "import numpy as np\n"
        "from sim.kernel import new_state, run_sim_checked\n"
        "from sim.outputs import SimResult\n"
        "from sim.ticks import PRICE_SCALE, TICK_SIZE_SCALED\n\n"
        f"{funcs_src}\n\n"
        "etime, bid_ticks, ask_ticks, pred = _build_fixed_sequence()\n"
        "result = run_sim_checked(\n"
        "    etime, bid_ticks, ask_ticks, pred, x_bps=0, state=new_state()\n"
        ")\n"
        "print(_hash_result(result))\n"
    )


def test_two_same_process_runs_hash_identical():
    etime, bid_ticks, ask_ticks, pred = _build_fixed_sequence()

    result_a = run_sim_checked(
        etime, bid_ticks, ask_ticks, pred, x_bps=0, state=new_state()
    )
    result_b = run_sim_checked(
        etime, bid_ticks, ask_ticks, pred, x_bps=0, state=new_state()
    )

    # Anti-vacuity: a run that never trades would hash two empty trade
    # logs identically no matter what, proving nothing about determinism
    # of the arithmetic this test exists to check.
    assert result_a.fill_count > 0, "vacuous: the fixture never trades"

    hash_a = _hash_result(result_a)
    hash_b = _hash_result(result_b)
    assert hash_a == hash_b


def test_subprocess_run_hashes_identical_to_the_parent_process(tmp_path):
    etime, bid_ticks, ask_ticks, pred = _build_fixed_sequence()
    parent_result = run_sim_checked(
        etime, bid_ticks, ask_ticks, pred, x_bps=0, state=new_state()
    )
    assert parent_result.fill_count > 0, "vacuous: the fixture never trades"
    parent_hash = _hash_result(parent_result)

    child_cache_dir = tmp_path / "numba-child-cache"
    script = _child_script()
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(MVP_ROOT),
        env={
            **os.environ,
            "PYTHONPATH": str(MVP_ROOT),
            # A FRESH cache dir, distinct from the parent test session's
            # own (conftest-pinned) one -- mirrors 06-RESEARCH.md Q8's own
            # measurement method (two distinct NUMBA_CACHE_DIRs).
            "NUMBA_CACHE_DIR": str(child_cache_dir),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"child process failed: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )

    child_stdout = proc.stdout.strip()
    # Anti-vacuity: a crashed child with empty stdout must not be allowed
    # to coincidentally "match" anything -- assert the shape of a real
    # sha256 hex digest BEFORE comparing values.
    assert re.fullmatch(r"[0-9a-f]{64}", child_stdout), (
        f"child stdout is not a well-formed 64-char hex digest: "
        f"{child_stdout!r}; stderr={proc.stderr!r}"
    )
    assert child_stdout == parent_hash


def test_unsliced_hash_is_sensitive_to_tail_garbage_sliced_hash_is_not():
    """A DETERMINISTIC, non-flaky proof of the fill-count hazard --
    deliberate sentinels in the unfilled tail, never a reliance on
    `np.empty`'s uninitialised memory happening to differ across repeated
    real runs (it is often zeroed on a fresh page, which would make this
    check unsatisfiable)."""
    etime, bid_ticks, ask_ticks, pred = _build_fixed_sequence()
    result = run_sim_checked(
        etime, bid_ticks, ask_ticks, pred, x_bps=0, state=new_state()
    )
    n = etime.shape[0]
    k = result.fill_count
    assert 0 < k < n, (
        "the fixture must have both real trades and a genuine unfilled "
        "tail for this test to be non-vacuous"
    )

    variant_a = {name: result.trade_log[name].copy() for name in _TRADE_LOG_COLUMNS}
    variant_b = {name: result.trade_log[name].copy() for name in _TRADE_LOG_COLUMNS}
    # Deliberate, DETERMINISTIC sentinels written into the UNFILLED TAIL
    # ONLY -- [:k] is never touched.
    variant_a["etime"][k:] = 111_111
    variant_b["etime"][k:] = 222_222
    for name in _TRADE_LOG_COLUMNS:
        assert np.array_equal(variant_a[name][:k], result.trade_log[name][:k]), name
        assert np.array_equal(variant_b[name][:k], result.trade_log[name][:k]), name

    # UNSLICED: hash the full preallocated columns, tail included. Must
    # DIFFER -- proves the tail is live in an unsliced hash.
    unsliced_hash_a = _hash_arrays(
        [variant_a[name] for name in _TRADE_LOG_COLUMNS] + [result.equity_scaled]
    )
    unsliced_hash_b = _hash_arrays(
        [variant_b[name] for name in _TRADE_LOG_COLUMNS] + [result.equity_scaled]
    )
    assert unsliced_hash_a != unsliced_hash_b, (
        "the unsliced hash must be sensitive to tail garbage -- this is "
        "the hazard sim/outputs.py's own docstring names"
    )

    # SLICED: hash via `_hash_result`, which slices every trade-log column
    # to `[:fill_count]` first. Must be IDENTICAL -- proves the slice
    # removes the hazard.
    result_a = result._replace(trade_log=variant_a)
    result_b = result._replace(trade_log=variant_b)
    sliced_hash_a = _hash_result(result_a)
    sliced_hash_b = _hash_result(result_b)
    assert sliced_hash_a == sliced_hash_b, (
        "the sliced hash must be INSENSITIVE to tail garbage -- this is "
        "the fix _hash_result's [:fill_count] slice provides"
    )
