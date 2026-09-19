"""THE entry point. Training, inference and the simulator reach the feature
kernel through this module and through nothing else (D-04-02, FEAT-01).

WHY THREE FUNCTIONS AND NOT ONE. `spec.md`'s single-code-path rule is a
claim about a RUNTIME property -- that the numbers a model is trained on
and the numbers a simulator scores are the same numbers -- and the only
honest way to check it is to call the one path in the three shapes the
three consumers actually have:

    for_training    ONE batch over a whole day. The trainer holds the day.
    for_inference   a chunked pass, carrying kernel state between chunks.
                    A serving loop holds a window, not a day.
    for_simulation  one row at a time, the most adversarial carry-state
                    case there is. The simulator holds one event.

Three wrappers around one identical call would make the byte-equality test
pass while proving nothing -- which is precisely the failure D-04-02 exists
to prevent -- so the difference in HOW they call is the thing the test
exercises: `test_the_three_call_sites_drive_the_kernel_differently` pins
that the kernel is entered once, once per chunk, and once per row.

THE EMISSION RULE, AND WHY THE SIMULATOR IS ONE GROUP LATE. A decision row
is the LAST row of its `etime` group (D-04-01), and a streaming caller
cannot know a row is the last of its group until a row with a larger
`etime` arrives. So every call site here defers: the decision row for
`etime = t` is emitted when the first row with `etime > t` is seen, and the
final group is flushed at end-of-stream. The batch call site reaches the
same rows by position, which is what makes the three comparable AT ALL --
and why every comparison aligns on `etime`, never on emission index.

That deferral is also what makes a chunk boundary INSIDE an `etime` group
harmless: the chunk's own last row is held back rather than emitted, and it
is superseded if the next chunk opens with the same `etime`.

NORMALIZATION IS ASYMMETRIC ON PURPOSE (D-04-06). `for_training` is the
only call site that may FIT parameters, and within the training segment it
applies the EXPANDING z-score. `for_inference` and `for_simulation` apply
the parameters they are GIVEN -- a loaded artifact -- and have no argument
that would let them fit. Asking either of them to normalize without an
artifact raises rather than quietly falling back to statistics of the data
being scored, which would be the leak `features/normalize.py` exists to
make impossible. The frozen transform is pointwise, so it cannot make a
chunked pass disagree with a batch one; the expanding one is not, which is
why the equality tests run with normalization off.

LABELS ARE NOT HERE. A label needs data AFTER the decision row -- day D's
long horizons read day D+1's quotes -- so a streaming call site cannot
produce one, and a batch call site that produced a null-tailed version
would be a SECOND label convention next to `features.build`'s correct one.
That is the same drift this module exists to prevent, one level up.
`labels=True` therefore raises and says where labels live.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np
import polars as pl

from features.event_stream import EVENT_SCHEMA, decision_row_index, event_arrays
from features.kernel import new_state, run_kernel_checked
from features.normalize import apply_normalization, expanding_z, fit_normalization
from features.reference import new_outputs
from features.tier import FEATURE_COLUMNS

__all__ = [
    "FEATURE_PASS_SCHEMA",
    "FeaturePass",
    "KernelState",
    "LabelsNotHereError",
    "NormalizationRequiredError",
    "Z_SUFFIX",
    "chunk_events",
    "compute_decision_rows",
    "decision_rows_to_frame",
    "event_row_stream",
    "for_inference",
    "for_simulation",
    "for_training",
]

#: The suffix of a normalized MODEL-INPUT column. Not a catalogue name:
#: `spec/features.toml` declares `normalization = "none"` for every feature
#: because that describes the STORED column, and these columns are never
#: stored (see `features/normalize.py`'s docstring).
Z_SUFFIX = "_z"

#: One decision row as this module emits it. A subset of
#: `features.tier.FEATURE_ROW_SCHEMA` -- the same names and dtypes, minus
#: the label and provenance columns the build adds around it.
FEATURE_PASS_SCHEMA: dict[str, pl.DataType] = {
    "etime": pl.Int64,
    "decision_source_rank": pl.Int8,
    "decision_seq": pl.Int64,
    **{name: pl.Float64 for name in FEATURE_COLUMNS},
    "warmup": pl.Boolean,
}

#: The kernel's carry-in/carry-out state (Plan 03's contract):
#: `(state_i64, state_f64, ring_t, ring_v)`.
KernelState = tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]

_EMITTED_FROM_EVENTS = {
    "etime": "etime",
    "decision_source_rank": "source_rank",
    "decision_seq": "seq",
}
_EMITTED_FROM_OUTPUTS = (*FEATURE_COLUMNS, "warmup")


class NormalizationRequiredError(ValueError):
    """Normalized outputs were asked for without a stored artifact.

    Raised instead of falling back to statistics of the data being scored:
    that fallback is the leak, and a loud failure is the only version of it
    that stays impossible.
    """


class LabelsNotHereError(ValueError):
    """`labels=True` on a module that deliberately cannot compute one."""


class FeaturePass(NamedTuple):
    """`(frame, state, normalization)` -- the decision rows, the carried-out
    kernel state (so a caller can resume), and the parameters that were
    fitted or applied (`None` when the pass was not normalized)."""

    frame: pl.DataFrame
    state: KernelState
    normalization: dict[str, tuple[int, float, float]] | None


@dataclass
class _DecisionEmitter:
    """The deferred last-row-of-group rule, shared by all three call sites.

    Holds ONE pending candidate -- the last row seen so far of the group
    that is still open. It is emitted when a larger `etime` arrives and
    discarded (superseded) when the same `etime` continues. Shared
    deliberately: the decision-row RULE is not what differs between the
    call sites, the call GRANULARITY is.
    """

    pending: dict[str, np.ndarray] | None = None
    pending_etime: int | None = None

    def feed(
        self, events: Mapping[str, np.ndarray], out: Mapping[str, np.ndarray]
    ) -> list[dict[str, np.ndarray]]:
        etime = events["etime"]
        if etime.shape[0] == 0:
            return []
        emitted: list[dict[str, np.ndarray]] = []
        if self.pending is not None and int(etime[0]) != self.pending_etime:
            emitted.append(self.pending)
        self.pending = None

        positions = decision_row_index(etime)
        if positions.size > 1:
            emitted.append(_select(events, out, positions[:-1]))
        self.pending = _select(events, out, positions[-1:])
        self.pending_etime = int(etime[positions[-1]])
        return emitted

    def flush(self) -> list[dict[str, np.ndarray]]:
        if self.pending is None:
            return []
        final, self.pending = self.pending, None
        return [final]


def _select(
    events: Mapping[str, np.ndarray],
    out: Mapping[str, np.ndarray],
    positions: np.ndarray,
) -> dict[str, np.ndarray]:
    """Copy the named positions out of one pass's arrays.

    COPIES, always. `event_arrays` hands back read-only views of polars
    memory and the row-by-row call site reuses one output buffer per row,
    so a view held across calls would be rewritten under the emitter.
    """
    selected = {
        name: np.asarray(events[source])[positions].copy()
        for name, source in _EMITTED_FROM_EVENTS.items()
    }
    for name in _EMITTED_FROM_OUTPUTS:
        selected[name] = np.asarray(out[name])[positions].copy()
    return selected


def _as_arrays(events) -> dict[str, np.ndarray]:
    """A merged-event frame or an already-converted array dict, as arrays."""
    if isinstance(events, pl.DataFrame):
        return event_arrays(events)
    missing = [name for name in EVENT_SCHEMA if name not in events]
    if missing:
        raise ValueError(
            f"features.api: event arrays are missing {missing}; the contract "
            f"is EVENT_SCHEMA {list(EVENT_SCHEMA)}"
        )
    return dict(events)


def _kernel_pass(
    events: Mapping[str, np.ndarray], state: KernelState, out: dict | None = None
) -> dict[str, np.ndarray]:
    """THE call into the kernel. Every call site funnels through here, and
    `tools/check_single_feature_path.py` exists so a later phase cannot
    quietly grow a second one."""
    return run_kernel_checked(events, state=state, out=out)


def _refuse_labels(labels: bool) -> None:
    if labels:
        raise LabelsNotHereError(
            "features.api does not compute labels: a label reads data AFTER "
            "its decision row (day D's long horizons read day D+1's quotes), "
            "which a streaming call site cannot see and which a batch call "
            "site could only fake with a null tail -- a SECOND label "
            "convention beside the correct one. Labels come from "
            "features.build.build_features_day, which owns the D+1 tail."
        )


def _resolve_normalization(
    *,
    normalize: bool | None,
    normalization: dict[str, tuple[int, float, float]] | None,
    caller: str,
) -> bool:
    """Whether to emit `_z` columns, refusing the one combination that
    would mean "make up parameters from the data in hand"."""
    wanted = normalization is not None if normalize is None else normalize
    if wanted and normalization is None:
        raise NormalizationRequiredError(
            f"{caller}: normalized outputs were asked for with no stored "
            "parameters. Only for_training may FIT parameters; inference and "
            "the simulator LOAD them "
            "(features.normalize.load_normalization) -- recomputing "
            "statistics from the rows being scored is the leak FEAT-05 "
            "forbids."
        )
    return wanted


def _frame(
    parts: list[dict[str, np.ndarray]],
    normalization: dict[str, tuple[int, float, float]] | None,
    *,
    expanding: bool,
) -> pl.DataFrame:
    """Assemble emitted parts into one frame, optionally with the
    normalized model-input columns beside the raw ones."""
    if parts:
        columns = {
            name: np.concatenate([part[name] for part in parts])
            for name in FEATURE_PASS_SCHEMA
        }
    else:
        # One empty float array per column is enough: the explicit `schema=`
        # below casts an EMPTY series to any declared dtype without
        # complaint. Per-column dtypes here would be dead code -- measured,
        # by deleting the bool patch this branch used to carry and watching
        # every test stay green (mutation (f) in the summary).
        columns = {name: np.empty(0, dtype=np.float64) for name in FEATURE_PASS_SCHEMA}

    frame = pl.DataFrame(
        {
            name: pl.Series(columns[name], dtype=dtype)
            for name, dtype in FEATURE_PASS_SCHEMA.items()
        },
        schema=dict(FEATURE_PASS_SCHEMA),
    )
    if normalization is None:
        return frame
    return frame.with_columns(
        [
            pl.Series(
                f"{name}{Z_SUFFIX}",
                expanding_z(columns[name])
                if expanding
                else apply_normalization(columns[name], normalization[name]),
                dtype=pl.Float64,
            )
            for name in FEATURE_COLUMNS
        ]
    )


def compute_decision_rows(
    events,
    *,
    state: KernelState | None = None,
    normalization: dict[str, tuple[int, float, float]] | None = None,
    normalize: bool | None = None,
    labels: bool = False,
) -> tuple[pl.DataFrame, KernelState]:
    """One batch: the whole of `events` through the kernel in a single
    call, decision rows selected, frozen normalization optionally applied.

    Returns `(frame, state)`; the state is carried OUT, so a caller holding
    a day in two halves gets the same rows as one holding it whole.
    """
    _refuse_labels(labels)
    wanted = _resolve_normalization(
        normalize=normalize, normalization=normalization, caller="compute_decision_rows"
    )
    arrays = _as_arrays(events)
    state = new_state() if state is None else state
    out = _kernel_pass(arrays, state)
    emitter = _DecisionEmitter()
    parts = emitter.feed(arrays, out)
    parts.extend(emitter.flush())
    return (
        _frame(parts, normalization if wanted else None, expanding=False),
        state,
    )


def for_training(
    events,
    *,
    state: KernelState | None = None,
    normalization: dict[str, tuple[int, float, float]] | None = None,
    fit_normalization: bool = False,
    labels: bool = False,
) -> FeaturePass:
    """The trainer's call: ONE batch over a whole day, and the ONLY call
    site permitted to FIT normalization parameters.

    NOTE the parameter `fit_normalization` SHADOWS the module-level
    `features.normalize.fit_normalization` inside this function's scope.
    The fit goes through `fit_normalization_from_frame`, which reads the
    module global from its own scope; calling `fit_normalization(...)`
    directly in here would call `True`.

    With `fit_normalization=True` the parameters are fitted on THIS pass's
    decision rows and the `_z` columns use the EXPANDING transform (row `t`
    normalized by training rows `<= t`). The fitted parameters come back in
    `FeaturePass.normalization` for the caller to persist with
    `features.normalize.write_normalization_artifact`: a real training
    segment spans several days and therefore several of these calls, so
    this function deliberately does not write an artifact of its own.
    """
    if fit_normalization and normalization is not None:
        raise ValueError(
            "for_training: both fit_normalization=True and stored parameters "
            "were given -- fitting and freezing are different runs"
        )
    _refuse_labels(labels)
    if not fit_normalization:
        frame, state = compute_decision_rows(
            events, state=state, normalization=normalization
        )
        return FeaturePass(frame, state, normalization)

    frame, state = compute_decision_rows(events, state=state)
    params = fit_normalization_from_frame(frame)
    return FeaturePass(
        frame.with_columns(
            [
                pl.Series(
                    f"{name}{Z_SUFFIX}",
                    expanding_z(frame[name].to_numpy()),
                    dtype=pl.Float64,
                )
                for name in FEATURE_COLUMNS
            ]
        ),
        state,
        params,
    )


def fit_normalization_from_frame(
    frame: pl.DataFrame,
) -> dict[str, tuple[int, float, float]]:
    """Welford parameters for every catalogued feature column of `frame`."""
    return fit_normalization({name: frame[name].to_numpy() for name in FEATURE_COLUMNS})


def for_inference(
    chunks: Iterable,
    *,
    state: KernelState | None = None,
    normalization: dict[str, tuple[int, float, float]] | None = None,
    normalize: bool | None = None,
    labels: bool = False,
) -> FeaturePass:
    """A serving loop's call: one kernel call per chunk, carrying state.

    The chunks may split an `etime` group anywhere -- the emitter holds the
    open group's candidate back rather than emitting it -- and the result
    is byte-identical to the batch pass for every chunk size, which is the
    property `test_chunk_boundaries_do_not_matter` pins.
    """
    _refuse_labels(labels)
    wanted = _resolve_normalization(
        normalize=normalize, normalization=normalization, caller="for_inference"
    )
    state = new_state() if state is None else state
    emitter = _DecisionEmitter()
    parts: list[dict[str, np.ndarray]] = []
    for chunk in chunks:
        arrays = _as_arrays(chunk)
        out = _kernel_pass(arrays, state)
        parts.extend(emitter.feed(arrays, out))
    parts.extend(emitter.flush())
    params = normalization if wanted else None
    return FeaturePass(_frame(parts, params, expanding=False), state, params)


def for_simulation(
    rows: Iterable[Mapping[str, object]],
    *,
    state: KernelState | None = None,
    normalization: dict[str, tuple[int, float, float]] | None = None,
    normalize: bool | None = None,
    labels: bool = False,
) -> Iterator[dict[str, object]]:
    """The simulator's call: ONE event at a time, yielding each decision row
    as soon as it is decidable.

    A generator, because that is the simulator's shape -- it holds one
    event and needs the decision for `etime = t` before it sees the rest of
    the day. It emits ONE GROUP LATE by construction (see the module
    docstring); aligning on `etime` rather than on emission index is what
    makes it comparable to the batch pass.

    The arguments are validated EAGERLY, before the first row is pulled: a
    missing normalization artifact must not surface a thousand events into
    a run.
    """
    _refuse_labels(labels)
    wanted = _resolve_normalization(
        normalize=normalize, normalization=normalization, caller="for_simulation"
    )
    state = new_state() if state is None else state
    return _simulate(rows, state, normalization if wanted else None)


def _simulate(
    rows: Iterable[Mapping[str, object]],
    state: KernelState,
    normalization: dict[str, tuple[int, float, float]] | None,
) -> Iterator[dict[str, object]]:
    emitter = _DecisionEmitter()
    buffers = {name: np.empty(1, dtype=_row_dtype(name)) for name in EVENT_SCHEMA}
    out = new_outputs(1)

    def emit(parts):
        for part in parts:
            row = {name: part[name][0] for name in FEATURE_PASS_SCHEMA}
            if normalization is not None:
                for name in FEATURE_COLUMNS:
                    row[f"{name}{Z_SUFFIX}"] = apply_normalization(
                        part[name], normalization[name]
                    )[0]
            yield row

    for event in rows:
        for name, buffer in buffers.items():
            buffer[0] = event[name]
        _kernel_pass(buffers, state, out)
        yield from emit(emitter.feed(buffers, out))
    yield from emit(emitter.flush())


def _row_dtype(name: str) -> np.dtype:
    dtype = EVENT_SCHEMA[name]
    if dtype == pl.Int64:
        return np.dtype(np.int64)
    if dtype == pl.Int8:
        return np.dtype(np.int8)
    return np.dtype(np.float64)


def chunk_events(events, size: int) -> Iterator[dict[str, np.ndarray]]:
    """Slice one merged stream into `size`-row chunks -- the inference call
    site's input when a recorded day is replayed through it."""
    if size <= 0:
        raise ValueError(f"chunk_events: size {size} must be positive")
    arrays = _as_arrays(events)
    total = arrays["etime"].shape[0]
    for start in range(0, total, size):
        yield {name: column[start : start + size] for name, column in arrays.items()}


def event_row_stream(events) -> Iterator[dict[str, object]]:
    """One merged stream as a sequence of single-event mappings -- a
    recorded day replayed exactly as a live feed would deliver it."""
    arrays = _as_arrays(events)
    total = arrays["etime"].shape[0]
    for index in range(total):
        yield {name: column[index] for name, column in arrays.items()}


def decision_rows_to_frame(rows: Iterable[Mapping[str, object]]) -> pl.DataFrame:
    """Collect a `for_simulation` stream into the same frame the batch and
    chunked call sites return -- the assembly the byte-equality test
    compares, and the shape a backtest writes out."""
    collected = list(rows)
    schema = dict(FEATURE_PASS_SCHEMA)
    if collected:
        for name in collected[0]:
            if name not in schema:
                schema[name] = pl.Float64
    if not collected:
        return pl.DataFrame(
            {name: pl.Series([], dtype=dtype) for name, dtype in schema.items()},
            schema=schema,
        )
    return pl.DataFrame(
        {
            name: pl.Series([row[name] for row in collected], dtype=dtype)
            for name, dtype in schema.items()
        },
        schema=schema,
    )
