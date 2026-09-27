"""The streams the leakage properties run against, in ONE place.

Both `tests/leakage/test_label_information_set.py` and
`tests/features/test_labels.py` drive `compute_labels` over generated
problems. Cloning the generator would let a bug in it make one suite pass
while the other fails -- so it lives here and both import it.

Lives in `tests/fixtures/` rather than `tests/leakage/` for the reason
recorded four times in this phase: a test package named after a real one
shadows it on `sys.path`.

TWO KINDS OF STREAM, for two kinds of question:

- `label_problem()` -- a hypothesis strategy over random-but-valid quote
  series and decision rows. Answers "is this ever false", over shapes
  nobody thought to write down.
- `horizon_probe()` -- ONE deterministic ladder of quotes laid around
  `t+h`, including the points that separate the conventions: `t+h-d`,
  `t+h+g/2`, `t+h+g`, `t+h+g+1ns`. Answers "exactly how far past `t` does
  this label reach", which a coarse random grid cannot: 04-04 already had
  a mutation survive because the generated stream had no exact `t+h` hit.
"""

from __future__ import annotations

import numpy as np
from hypothesis import strategies as st

from data.time_ns import NS_PER_SECOND

#: The hypothesis grid's step menu, in seconds. `0` makes ties, `31`
#: makes a gap longer than the 30 s default threshold -- both paths are
#: reachable only because they are listed here.
_STEP_SECONDS = (0, 1, 2, 5, 10, 31)


def i64(values) -> np.ndarray:
    return np.asarray(values, dtype=np.int64)


def f64(values) -> np.ndarray:
    return np.asarray(values, dtype=np.float64)


@st.composite
def quote_series(draw, max_quotes: int = 30):
    """Ascending quote etimes on a COARSE grid (ties and exact `t+h` hits
    have to actually occur, or the exact-match path is covered only by the
    example tests) with positive mids."""
    n = draw(st.integers(min_value=2, max_value=max_quotes))
    steps = draw(st.lists(st.sampled_from(_STEP_SECONDS), min_size=n, max_size=n))
    etimes = np.cumsum(i64(steps)) * NS_PER_SECOND
    mids = (
        f64(
            draw(
                st.lists(
                    st.integers(min_value=990, max_value=1010),
                    min_size=n,
                    max_size=n,
                )
            )
        )
        / 10.0
    )
    return etimes.astype(np.int64), mids


@st.composite
def label_problem(draw):
    """`(decision_etime, decision_mid, quote_etime, quote_mid)`.

    `decision_mid` is the PREVAILING mid at `t`, exactly as the kernel
    computes it -- NaN before the first quote. Deriving it here rather
    than drawing it independently is what keeps the problem one a real
    build could hand to `compute_labels`.
    """
    quote_etime, quote_mid = draw(quote_series())
    n_rows = draw(st.integers(min_value=1, max_value=8))
    span = int(quote_etime[-1]) + 5 * NS_PER_SECOND
    decision_etime = i64(
        sorted(
            draw(
                st.lists(
                    st.integers(min_value=0, max_value=max(span, 1)),
                    min_size=n_rows,
                    max_size=n_rows,
                )
            )
        )
    )
    idx = np.searchsorted(quote_etime, decision_etime, side="right") - 1
    decision_mid = np.where(idx < 0, np.nan, quote_mid[np.maximum(idx, 0)])
    return decision_etime, decision_mid.astype(np.float64), quote_etime, quote_mid


def horizon_probe(horizon_ns: int, gap_ns: int, *, exact_match: bool = True):
    """One decision row at `t`, and a ladder of quotes around `t + h`.

    Returns `(decision_etime, decision_mid, quote_etime, quote_mid,
    offsets)` where `offsets[j] = quote_etime[j] - t` -- the offset is what
    every assertion is phrased in, because "how far past `t` can a row be
    and still matter" is the question.

    SHAPE, and why each part is there:

    - quotes step by at most `gap_ns // 4` from `t - h` to `t + 2*gap_ns`,
      so the BASELINE has no big gap anywhere and every null in a perturbed
      run is a null the perturbation caused;
    - `exact_match=True` puts a quote EXACTLY at `t+h` and none in
      `(t+h-d, t+h)`; `exact_match=False` leaves `(t+h-d, t+h]` empty so
      the prevailing quote sits strictly inside the horizon. BOTH are
      needed, and 04-04 is why: `side="left"` keeping the `-1` and
      `side="left"` without it are two different wrong as-of rules, and
      each one is invisible on the probe that catches the other;
    - quotes exist at `t+h+g/2` (inside the mask's reach) and past
      `t + h + g` (outside it), which is the pair that separates the
      label's VALUE information set from its NULL MASK's.

    Mids are all distinct, so "perturbing this quote changed the label"
    cannot be an accident of two quotes sharing a price.
    """
    h = int(horizon_ns)
    g = int(gap_ns)
    step = max(g // 4, 1)
    d = max(min(h // 4, g // 2), 1)

    offsets = set(range(-h, h + 2 * g + 1, step))
    offsets |= {-h, 0, h - d, h + g // 2, h + g, h + g + 1, h + 2 * g}
    if exact_match:
        # Nothing in `(h-d, h)`, and a quote exactly AT `h`.
        offsets = {o for o in offsets if not (h - d < o < h)} | {h}
    else:
        # Nothing in `(h-d, h]`: the prevailing quote at `t+h` is the one
        # at `h-d`, at a known distance inside the horizon.
        offsets = {o for o in offsets if not (h - d < o <= h)}
    offsets = sorted(offsets)

    t = 100 * (h + g)
    quote_etime = i64([t + o for o in offsets])
    quote_mid = f64([100.0 + 0.01 * (i + 1) for i in range(len(offsets))])

    zero = offsets.index(0)
    decision_etime = i64([t])
    decision_mid = f64([quote_mid[zero]])
    return decision_etime, decision_mid, quote_etime, quote_mid, i64(offsets)
