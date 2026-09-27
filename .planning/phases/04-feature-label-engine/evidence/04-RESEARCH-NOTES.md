# Phase 4 (Feature & Label Engine) — research notes

Read-only. Nothing was written to `/Volumes/ProjectsSSD/aihedgefund/repo`; no git commands were
run there; the capture daemon was not signalled (it *was* running during every timing below — see
the caveat in Q2). All scripts live in this scratchpad; `NUMBA_CACHE_DIR` was pointed here so
`cache=True` never wrote into the repo.

Environment (from the project venv `/Volumes/ProjectsSSD/aihedgefund/repo/mvp/.venv/bin/python3`):
Python 3.13.3, polars 1.41.2, numpy 2.4.6, numba 0.65.1. Host: 8 cores, 32 GB RAM, macOS (Darwin
25.5.0). `pl.thread_pool_size() == 8`.

**Ground truth for the day used throughout** (`symbol=BTCUSDT`, `date=2026-09-13`):

| | rows | `etime` sorted in file order | `seq` | distinct `etime` |
|---|---|---|---|---|
| `stream=bookTicker` | 17,167,290 | yes | 0…N−1, unique, sorted | 6,863,586 |
| `stream=trade` | 1,409,705 | yes | 0…N−1, unique, sorted | 329,580 |
| merged | 18,576,995 | — | — | 6,864,853 |

The task brief says "~26M rows/day"; the real day is **18.58M**. Q2 is measured on the real 18.58M;
Q3 is measured on both a 26M synthetic array (per the brief) and the real 18.58M merged array.

---

## Q1 — Cont–Kukanov–Stoikov OFI for a BBO-only feed

### Short answer

**No adaptation is needed. CKS *is* a top-of-book measure.** The paper's own Section 2.1 opens
"We focus on 'Level I order book': the limit orders sitting at the best bid and ask", and the
empirical work uses NYSE TAQ consolidated quotes — best bid/ask price and size only, exactly the
shape of a Binance `bookTicker` row. The three-case description and the indicator formula are two
renderings of the same expression; the indicator form needs no branching because at an unchanged
price **both** indicators fire.

Per-update contribution, with `b`/`a` = best bid/ask price and `q_b`/`q_a` their sizes, and
subscript `n-1` the previous BBO observation:

```
e_n =  I{b_n >= b_{n-1}} * q_b[n]   -  I{b_n <= b_{n-1}} * q_b[n-1]
     - I{a_n <= a_{n-1}} * q_a[n]   +  I{a_n >= a_{n-1}} * q_a[n-1]
```

Expanded to the three cases per side:

| bid side | contribution | ask side | contribution |
|---|---|---|---|
| `b_n > b_{n-1}` (price-improving bid) | `+q_b[n]` | `a_n < a_{n-1}` (price-improving offer) | `−q_a[n]` |
| `b_n = b_{n-1}` (resize / cancel / hit) | `+(q_b[n] − q_b[n-1])` | `a_n = a_{n-1}` | `−(q_a[n] − q_a[n-1])` |
| `b_n < b_{n-1}` (bid level swept or pulled) | `−q_b[n-1]` | `a_n > a_{n-1}` | `+q_a[n-1]` |

`e_n` is in **size units** (contracts/base for this venue — BTC for BTCUSDT-perp), not notional.

**Sign convention: positive = net buying pressure.** This agrees with the project's existing
`trade_side.py` (`SIGN_MAKER_IS_SELLER = +1`, aggressor bought), so `ofi` and `trade_flow` will
carry the same sign. Note the easy inversion: *a falling ask is negative*, because CKS classes
`a_n < a_{n-1}` as "an increase in supply". Prose on the web that says "a lower ask is buy-side
pressure" is describing the inner (pre-negation) term, not `e_n`.

### Evidence

**Primary — Cont, Kukanov & Stoikov, "The price impact of order book events."** arXiv:1011.6402
(published *J. Financial Econometrics* 12(1), 2014). https://arxiv.org/abs/1011.6402 —
Section 2.1 "Variables", the (unnumbered) display after "We define the variable e_n which measures
the contribution of the n−th event to the size of bid and ask queues". Verbatim from `pdftotext`
of https://arxiv.org/pdf/1011.6402 (lines 203–232 of the extraction):

> `en = I{PnB ≥P B n−1} qnB − I{PnB ≤P B n−1} q B n−1 − I{PnA ≤P A n−1} qnA + I{PnA ≥P A n−1} q A n−1`
>
> "Note that if q B increases but P B remains the same, we assign en = qnB − q B n−1 , representing
> the size that was added at the bid. If q B decreases, we also assign en = qnB − q B n−1 ,
> representing the size that was removed from the bid, whether due to a market sell or cancel buy
> order. If P B increases, we let en = qnB , representing the size of a price-improving limit order.
> If P B decreases, we let en = q B n−1 , representing the size that was removed, whether due to a
> market order or a cancellation. The same classification is done for events on the ask side, with
> signs reversed."

Same section, line 165: *"We focus on 'Level I order book': the limit orders sitting at the best
bid and ask."* Section 3.1 confirms the data is NYSE TAQ consolidated quotes. **This settles that
BBO-only is the native case, not a degradation.**

The paper defines `OFI_k = Σ_{n=N(t_{k-1})+1}^{N(t_k)} e_n` — an interval sum. The catalogue's
`ofi` entry says *"between consecutive updates"*, i.e. the **per-update `e_n`**, not `OFI_k`.
See the ambiguity list.

**Reference formulation #1 (peer-reviewed restatement, explicit three-case form with equation
numbers)** — Cont, Cucuringu & Zhang, "Multi-Level Order-Flow Imbalance in a Limit Order Book",
Oxford ORA:
https://ora.ox.ac.uk/objects/uuid:9b7d0422-4ef1-48e7-a2d4-4eaa8a0a7ec1/files/m89dedb16194e627a2c92d14e3329bd48
Equations (2)–(4), verbatim from `pdftotext` (`r¹` = level-1 bid size, `q¹` = level-1 ask size):

> `e_n := ΔW(τ_n) − ΔV(τ_n)`, (2)
> `ΔW(τ_n) = r¹(τ_n)` if `b¹(τ_n) > b¹(τ_{n−1})`; `r¹(τ_n) − r¹(τ_{n−1})` if `=`; `−r¹(τ_{n−1})` if `<`. (3)
> `ΔV(τ_n) = −q¹(τ_{n−1})` if `a¹(τ_n) > a¹(τ_{n−1})`; `q¹(τ_n) − q¹(τ_{n−1})` if `=`; `q¹(τ_n)` if `<`. (4)

and on sign: *"OFI(t_{k−1},t_k) is positive if and only if the aggregated buying pressure at the
best quotes is greater than the aggregated selling pressure at the best quotes."*

**Reference implementation #2 (executable)** — QuestDB SQL cookbook, "Order Flow Imbalance":
https://questdb.com/docs/cookbook/sql/finance/order-flow-imbalance

```sql
CASE WHEN bid_price > prev_bid_price THEN bid_volume
     WHEN bid_price < prev_bid_price THEN -prev_bid_volume
     ELSE bid_volume - prev_bid_volume END
-
CASE WHEN ask_price < prev_ask_price THEN ask_volume
     WHEN ask_price > prev_ask_price THEN -prev_ask_volume
     ELSE ask_volume - prev_ask_volume END AS ofi_event
```

It filters `WHERE prev_bid_price IS NOT NULL`, i.e. drops the first row of the partition.

**Verified numerically, not just read.** I implemented the paper's indicator form as an
`@njit(cache=True)` kernel and the QuestDB CASE form as a polars `shift(1)` expression and ran
both over all 17,167,290 real bookTicker rows for 2026-09-13
(`scratchpad/bench_real.py`, function `cks_ofi`):

```
njit-CKS vs polars-QuestDB-form agree: True   n compared: 17,167,289   max abs diff: 2.84e-14
OFI: n=17167290  nan=1  zeros(after row 0)=798  mean=-0.0001  std=0.4975  p1=-1.338  p99=1.331
```

The single NaN is row 0 (no previous quote). 798 exact zeros in 17.2M — and separately I measured
**0 rows on this day are byte-identical to their predecessor** in all four of
`(bid_price, bid_qty, ask_price, ask_qty)`, so the 798 zeros are genuine offsetting moves, not
duplicate messages.

Throughput of the OFI kernel on the merged 18.58M array: **0.030 s, 628 M rows/s** (second call;
first call includes JIT compile).

### The two cases you asked to call out explicitly

**First update of a day (no `n-1`).** `e_1` is *undefined* — it is not zero. Three options, and
this is a decision Phase 4 must make and record, not something to infer:
1. **Emit null** (my recommendation) — honest, and the row is dropped by any model that requires
   the feature. Matches QuestDB's `WHERE prev_bid_price IS NOT NULL`.
2. Emit `0.0` — cheap, but manufactures a "no imbalance" observation that was never measured, and
   pollutes any rolling sum seeded from it.
3. Seed from day D−1's last quote. This is **not** an information-set violation (D−1's last quote
   precedes D's first), but it makes day D's features depend on day D−1's partition, which breaks
   per-partition reproducibility and per-day parallel rebuilds.

Cost of option 1 on this day: **1 null row out of 17.17M (0.0000058%)**. There is no reason to take
on the cross-day coupling of option 3 for that.

**A quote that repeats unchanged.** No special-casing needed and none should be added: both the
`≥` and `≤` indicators fire on each side, the expression collapses to
`(q_b[n] − q_b[n-1]) − (q_a[n] − q_a[n-1]) = 0`, and `e_n` is exactly `0.0` with no floating-point
residue (identical operands cancel). Measured: 0 such rows on 2026-09-13, so this is a
correctness-not-frequency concern — but a `bookTicker` stream *can* emit them across a reconnect,
so the kernel must not assume "every update changes something".

### BBO-only limitation to record

A multi-tick jump only shows the *new* best's size; the depth that was swept or added at the
levels in between is invisible. CKS accepted this (TAQ Level-I); the MLOFI paper above is the
documented fix (out-of-sample fit improves with each extra level) and is the citation to reach for
if L2 is ever captured. On this day 97.5% of quotes sit at a 1-tick spread, so multi-tick jumps
are rare and the limitation is small in practice — but it is a real, named model error, not zero.

**"Consecutive updates" means consecutive in `(etime, seq)` order within the bookTicker stream —
not in `etime` order.** 17.17M updates share only 6.86M distinct `etime` values (2.5 updates per
millisecond on average), so `etime` alone does not order them. `seq` is monotone in file order and
unique, so file order is already the right order; the kernel must simply not re-sort by `etime`.

**Recommendation:** implement the indicator form verbatim (four `if`s, no `elif` chain — that is
what makes the unchanged-price case fall out for free), positive = buy pressure, **null on the
first update of each partition**, no cross-day seeding; assert in CI that a repeated quote yields
exactly `0.0` and that the njit kernel matches the polars `shift(1)` expression bit-for-bit.

---

## Q2 — Merging two curated streams into one chronological event array

### Short answer

**Use `polars.DataFrame.merge_sorted`.** It is the fastest of the three on the real day (0.61–0.77 s
for 18.58M rows), has the lowest peak RSS, is one line, and — because it is a *stable* merge that
takes from the left frame on ties — it produces **exactly** `(etime, source_rank, seq)` order when
the left frame is the rank-0 stream. No explicit `source_rank` column or sort key is needed at all;
the rank is encoded by which frame you pass as `self`. All three variants produced **byte-identical
output and a verified strict total order**.

### Measurements

Every variant ran in its own process (`getrusage(RUSAGE_SELF).ru_maxrss`, bytes on macOS), 3
repeats, timing the merge only — parquet read is reported separately as `load_s ≈ 0.25–0.38 s`.
Script: `scratchpad/bench_merge2.py` (variants a, b, d) and `scratchpad/bench_merge.py`
(variant c). Both frames were first projected onto one 7-column event schema
(`etime i64, src i8, seq i64, f0..f3 f64`).

| variant | merge wall-clock (best of 3) | peak RSS | strict total order | output identical |
|---|---|---|---|---|
| (a) `pl.concat(how="vertical")` + `.sort(["etime","src","seq"])` | **1.009 s** (1.79/1.45/1.01) | 3.05 GB | yes, 0 violations | yes |
| (b) `b.merge_sorted(t, key="etime")` | **0.610 s** (0.66/0.61/0.61) | **2.74 GB** | yes, 0 violations | yes |
| (c) numpy `searchsorted` stable interleave → polars frame | **4.754 s** | 2.55 GB | yes, 0 violations | yes |
| (c′) same, int64 key columns only, no float payload | 2.268 s | 2.29 GB | yes | — |
| (d) `@njit` two-pointer merge into preallocated arrays | **0.471 s** (0.47/0.61/0.79) | 3.27 GB | yes, 0 violations | yes |

Identity was checked by a digest over `(sum etime, sum src, sum seq, first/last 1000 f0)` — all
four agree exactly. "Strict total order" was *verified, not asserted*: over all 18,576,994 adjacent
pairs, `(Δetime>0) ∨ (Δetime=0 ∧ Δsrc>0) ∨ (Δetime=0 ∧ Δsrc=0 ∧ Δseq>0)` held everywhere.

Tie behaviour of `merge_sorted` was pinned on a 5-row toy first (`scratchpad/toy_merge_sorted.py`):
with `a.merge_sorted(b, key="etime")` all of `a`'s rows at a tied `etime` precede all of `b`'s, and
swapping the receiver reverses it. So the left frame **is** `source_rank = 0`.

One extra measurement that decides the recommendation: after `merge_sorted`, extracting all six
columns with `.to_numpy()` for the `@njit` pass cost **0.000 s** — polars hands back zero-copy
views, so the "the engine wants raw arrays anyway, so merge in numba" argument does not apply.
End-to-end `read_parquet → merge_sorted → to_numpy` is 0.38 + 0.77 + 0.00 ≈ 1.15 s for the day.

Caveat: the capture daemon (PID 72546) was running throughout; variant (a) and (d) show 1.8× run-to-run
spread, which is why best-of-3 is reported. `merge_sorted` was the most *stable* (0.610–0.661 s).

### Why (c) is slow, given that it "should" be O(n)

`np.searchsorted(trade_etime, bt_etime)` is 17.2M × log₂(1.4M) ≈ 21 comparisons per row,
single-threaded, and the subsequent `dst[positions] = src` writes are scattered across a 150 MB
destination — a cache-miss per row. The numba two-pointer (d) does the same job with sequential
writes and beats it 10×. If a numpy-level merge is wanted, do the two-pointer, never `searchsorted`.

### `source_rank` does not exist yet, and `seq` cannot substitute for it

`grep -rn source_rank` across `mvp/` returns nothing: the column is in neither curated schema and
is not mentioned in `spec.md`. And it genuinely cannot be skipped — `mvp/data/ingest/curated_build.py`
`materialize_seq` assigns `seq` as a **fresh per-partition row index** (`with_row_index("seq")`),
so both streams start at `seq = 0`. `(etime, seq)` across the merged frame is therefore *not* a
total order. Whatever Phase 4 calls it, the stream identity must be part of the key.

**The *value* of that rank turns out to be output-neutral for Phase 4's four declared features —
measured, see ambiguity #1 — but the rank must still be pinned, because it is what makes the merged
artefact a reproducible total order.**

**Recommendation:** `rank0_frame.merge_sorted(rank1_frame, key="etime")`, with the rank order
settled by the decision above and the `source_rank` column materialized explicitly (as a literal
on each frame before the merge) so the total order is inspectable in the written artefact rather
than implied by argument position.

---

## Q3 — numba trailing-window accumulation without per-row allocation

### Short answer

**Ring buffer over two preallocated arrays.** It is the idiomatic answer *and* the fastest by a
wide margin: **366 M rows/s** on the real merged day, 7× faster than `typed.List` and 2× faster
than the two-pointer-over-the-whole-array approach. `typed.List` and `structref` are both wrong
here — `typed.List` is 7× slower and reflection-boxed, and `structref` buys encapsulation the hot
path does not need. Neither allocates per row in the malloc sense, but both cost per-row indirection.

The surprise, and the reason to prefer the ring buffer over the "obvious" answer: the two-pointer
design (keep a trailing cursor `lo` into the same 18.6M-row array; no buffer at all) is elegant and
allocation-free, but its `lo` cursor drags a **second cache-miss stream across a 150 MB array**.
The ring buffer only ever touches the ~5 K trades currently inside the window, which fits in L1.

### Measurements

Real merged day (18,576,995 rows, 1,409,705 of them trades), `scratchpad/bench_real.py`,
best of 3 after a warm-up call to exclude JIT compile:

| design | wall-clock | throughput | notes |
|---|---|---|---|
| ring buffer, `cap = 1<<16`, **int64** accumulator | **0.046 s** | **406 M rows/s** | the recommendation |
| ring buffer, `cap = 1<<16`, float64 accumulator | 0.048 s | 384 M rows/s | max window occupancy **5,092** |
| two-pointer over the event array | 0.103 s | 180 M rows/s | output bit-identical to ring |

The int64 accumulator is **not** a throughput cost on the ring buffer — it is 6% *faster* (integer
add beats float add, and the ring's payload array is `int64` instead of `float64`). The 11%
slowdown seen below is a two-pointer-only effect. Take the int64 accumulator unconditionally.

Also measured: **784,343 of 18,576,995 event rows (4.2%) have an empty 1-second window** — the
stream has 27 inter-quote gaps longer than 1 s on this day. The kernel returns `0.0` there, not
null. That is correct ("no signed volume traded in the last second") but it is a value the model
cannot distinguish from "flow exactly cancelled", so it should be a stated behaviour, and an
`n_trades_in_window` companion is the cheap way to make the two distinguishable if that matters.

Synthetic 26M rows (the brief's size), 7.6% trades to match the real ratio,
`scratchpad/bench_window.py`:

| design | wall-clock | throughput |
|---|---|---|
| ring buffer, `cap = 1<<20` | **0.076 s** | **343 M rows/s** |
| two-pointer, float64 accumulator | 0.224 s | 116 M rows/s |
| two-pointer, int64 accumulator (qty × 1e8) | 0.250 s | 104 M rows/s |
| `numba.typed.List` | 0.529 s | 49 M rows/s |

Sizing the ring: max occupancy of a 1-second window on the real day is **5,092 trades**.
`cap = 1<<16 = 65,536` gives 12.9× headroom and costs 65,536 × 16 B = **1 MB**. The kernel must
still detect overflow (`tail == head` after an append) and fail loudly rather than silently wrap —
a silently-wrapped ring is a feature that looks fine and is wrong.

### Minimal working sketch (compiles and runs under `@njit(cache=True)`, numba 0.65.1)

```python
import numpy as np
from numba import njit

WIN = 1_000_000_000  # 1 s in ns

@njit(cache=True)
def trade_flow_1s(etime, src, qty, side, out, cap):
    """Signed trade volume over the trailing window (t-1s, t], one value per event row.

    etime int64[:] ns, ascending (etime, source_rank, seq) order
    src   int8[:]   0 = bookTicker, 1 = trade
    qty   float64[:], side int8[:]  (payload columns of the merged event array)
    out   float64[:] preallocated, len == len(etime)
    Returns (status, max_occupancy); status < 0 means the ring overflowed.
    """
    rb_t = np.empty(cap, np.int64)
    rb_v = np.empty(cap, np.float64)
    head = 0
    tail = 0
    acc = 0.0
    maxfill = 0
    for i in range(etime.shape[0]):
        t = etime[i]
        if src[i] == 1:                      # a trade: push onto the ring
            rb_t[tail] = t
            rb_v[tail] = qty[i] * side[i]
            acc += rb_v[tail]
            tail = (tail + 1) % cap
            if tail == head:                 # full: refuse, never wrap silently
                return -1, maxfill
        t0 = t - WIN                         # evict everything at or before t-1s
        while head != tail and rb_t[head] <= t0:
            acc -= rb_v[head]
            head = (head + 1) % cap
        fill = (tail - head) % cap
        if fill > maxfill:
            maxfill = fill
        out[i] = acc
    return 0, maxfill
```

The `while rb_t[head] <= t0` condition is what makes the window **`(t − 1s, t]`** — half-open at
the old end, closed at the new. Flipping `<=` to `<` gives `[t − 1s, t]`. That is a decision, see
the ambiguity list.

The `(tail + 1) % cap` modulo costs a division; with `cap` a power of two, `& (cap - 1)` is faster
and numba will not do that rewrite for you, because `cap` is a runtime value. Making `cap` a
compile-time constant (module-level global, or a `numba.literally` specialization) is the obvious
next optimization — not needed at 366 M rows/s.

### numba-version pitfalls found or confirmed on 0.65.1

- **`cache=True` writes next to the defining source file.** Set `NUMBA_CACHE_DIR` before import, or
  a Phase-4 CI run will scatter `__pycache__/*.nbc`/`*.nbi` into the repo. Confirmed working: the
  benchmark produced `bench_real.tw_ring-20.py313.1.nbc` under the scratchpad cache dir.
- **`numba.typed.List` compiles but is the wrong tool** — 49 M rows/s vs 366. Its append path goes
  through the typed-container runtime, not a raw store.
- **numpy 2.4.6 is the ceiling.** numba 0.65.1 pins `numpy < 2.5`; the project lockfile is already
  at 2.4.6, which is the last compatible minor. Any drift breaks every `@njit` *import*, not just
  its results.
- **Do not pass `pl.Datetime` columns into a kernel.** The project's ns-int64 convention already
  covers this; `.to_numpy()` on an `Int64` polars column with no nulls is zero-copy (measured at
  0.000 s for six columns of 18.58M rows). A *nullable* column forces a copy and a float cast — so
  the merged event array's hot-path columns must be non-nullable by construction.
- **float64 running-sum drift is real but tiny in absolute terms, and unbounded in relative terms.**
  Measured over 26M synthetic rows against an exact int64 accumulator (`qty × 1e8`, which is exact:
  100% of real `qty` and `bid_qty`/`ask_qty` values are integral at 1e-8):
  `max_abs_err = 5.12e-12`, `final_abs_err = 2.61e-12` — negligible against a window sum of order 1.
  But `max_rel_err = 5.1`, because the window sum passes through ~0 constantly. **Anything that
  divides by `trade_flow` (a normalized flow ratio, say) must not be built on the float64 running
  sum.** On the ring buffer the int64 accumulator is *free* (6% faster, see the table above), and
  it additionally makes the feature bit-identical across merge tie orders — see ambiguity #1. Take it.

**Recommendation:** ring buffer over two preallocated arrays, `cap` a power of two with an overflow
guard and the observed max-occupancy logged per partition; int64 scaled accumulator (`qty × 1e8`) —
free on throughput, exact, and merge-order-independent; `NUMBA_CACHE_DIR` pinned outside the repo
in CI.

---

## Spec ambiguities that need a decision before implementation

Beyond the already-pinned `trade_flow` 1s window length. Ordered by how much downstream work a
late change would invalidate.

**1. `source_rank` — which stream wins an `etime` tie — is undefined. It must be pinned, but it is
*output-neutral* for the four declared features, and the one place it leaks through is a
float64 artefact that the int64 accumulator removes.**

94.3% of trades (1,329,285 / 1,409,705) share an `etime` with a bookTicker update, because both
clocks come from Binance's *millisecond* `E`/`T` fields. `spec.md:68` pins decision points to "the
last row of each `etime` group … the last one (latest in arrival order within the file)" — written
when there was one file; with two merged streams "arrival order within the file" has no referent.

I ran the whole day both ways (`b.merge_sorted(t)` vs `t.merge_sorted(b)`), computed all four
features, took the last row of each `etime` group, and compared
(`scratchpad/rank_test.py`, `scratchpad/rank_test2.py`):

| feature at the decision row | identical across rank orders? |
|---|---|
| `mid` | **yes — bit-identical**, 0 of 6,864,853 rows differ |
| `imb_top` | **yes — bit-identical**, 0 differ |
| `ofi` | **yes — bit-identical**, 0 differ |
| `trade_flow` (float64 accumulator) | no — 6,528,439 / 6,864,853 (95%) differ |
| `trade_flow` (int64 accumulator) | **yes — bit-identical**, 0 differ |

The reason all four *should* be neutral: `spec.md:68` already puts every event at `etime = t`
inside the decision row's information set (preceding rows in the group update state), and none of
the four reads across streams *within* a group — `mid`/`imb_top`/`ofi` are quote-only, and
`trade_flow` membership is by `etime` alone.

The reason `trade_flow` nonetheless moved: rank order changes the interleaving of `+=` and `-=` in
the float64 running sum, and float addition is not associative. The differences are pure rounding —
**max absolute 1.17e-13 BTC, max relative 1.17e-10** (on rows where the value exceeds 1e-6). Under
the int64 accumulator the feature is bit-identical either way. So this is not a modelling question;
it is a **reproducibility** question, and the int64 accumulator recommended in Q3 answers it.

What the rank *does* change is which row is the decision row: **329,580 of 6,864,853 decision rows
are trade rows under quotes-first** (exactly the count of distinct trade `etime`s — true by
construction, not a data finding), versus **1,267 under trades-first**. That matters only for a
future feature that reads the decision row's own payload rather than carried state. Pin the rank,
record it in `spec.md`, and add the both-ways equality as a CI property test — but this does not
block implementation.

Note also that Phase 3's `allow_exact_matches=False` is **not** in conflict with a quotes-first
rank. That ruling governs using a quote to *classify a trade's side* (a per-trade asof lookup);
`spec.md:68` separately and deliberately puts same-`etime` events inside the decision-point
information set. Two different rules, no contradiction.

**2. `ret_*_mid` — "`mid_{t+h}`" names a row that does not exist.** No quote lands exactly at
`t + 10s`. Two conventions:
- prevailing quote at or before `t + h` (`searchsorted(..., "right") - 1`), or
- first quote at or after `t + h` (`searchsorted(..., "left")`).

Measured on 2026-09-13 for `h = 10s`: the two disagree on **0.26%** of rows, correlation 0.999907.
Small, but not zero, and it is exactly the kind of unrecorded choice that makes two runs
irreproducible. The *prevailing* convention is the one consistent with `trade_side.py`'s existing
`strategy="backward"` treatment of "the quote in force at a time".

**3. `ret_*_mid` at the end of a partition.** Rows with no `t + h` inside the day:
1,086 (0.0063%) at 10s; 116,418 (0.68%) at 10min. Null, or reach into day D+1? Reaching across is
not a leakage violation but does couple partitions (same trade-off as OFI's first row). Nulling
0.68% of a day for the 10min diagnostic label is cheap; the roadmap should say so explicitly.

**4. The `mid` catalogue note "Treat sub-tick stickiness flag separately" names a flag that exists
nowhere in the repo.** I measured what it is pointing at: **97.5% of quotes sit at a 1-tick spread
(tick = 0.1 USDT), so `mid` lands on a half-tick 98.8% of the time.** The consequence is that the
labels are heavily quantized:

| label | std | fraction exactly 0 | std in half-ticks |
|---|---|---|---|
| `ret_1s_mid` | 6.88e-05 | **62.5%** | 106 |
| `ret_10s_mid` | 1.85e-04 | **29.7%** | 285 |
| `ret_1min_mid` | 3.83e-04 | 8.2% | 589 |
| `ret_10min_mid` | 9.53e-04 | 0.2% | 1466 |

The primary label `ret_10s_mid` is exactly zero for nearly a third of rows. That is survivable for
a regression target but it is a property the loss function and every IC/Sharpe statistic must be
chosen knowing about — and `ret_1s_mid` at 62.5% zeros is barely a continuous variable at all.
Either define the flag (what it is, what consumers do with it) or delete the note; a dangling
pointer in the catalogue is worse than neither.

**5. `trade_flow` window endpoints.** `(t − 1s, t]` or `[t − 1s, t)`, and specifically: **is a trade
whose `etime` equals the decision row's `etime` inside the feature's information set?** This is the
same question as #1 wearing different clothes, and it must get the same answer. The kernel sketch
above implements `(t − 1s, t]`; either is defensible, neither is declared.

**6. Units are undeclared for `ofi` and `trade_flow`.** Both come out in base-asset size (BTC) as
written. The alternative — notional USDT (`qty × price`) — is a different feature with different
scale and different stationarity, and under the catalogue's own rules would need a different name.
`unit_registry.toml` covers archive CSV column conventions only; it says nothing about feature
units. Declare it, and declare it in the `definition` string so `check_spec_diff.py` guards it.

**7. `imb_top`'s "Undefined when both sizes are 0" is a dead note on this venue.** Measured on
17.17M real rows: **`bid_qty == 0` in 0 rows, `ask_qty == 0` in 0 rows**, and
**0 crossed-or-locked books (`bid >= ask`)**. Binance `bookTicker` does not publish an empty side.
Keep the guard as a hard assertion that fires loudly (it would signal a feed corruption), but do not
build a null-propagation path for a case that has never occurred — and say in the note that it is an
assertion, not an expected branch. The same applies to `mid` and crossed books.

**8. `ofi` per-update vs interval-summed.** The catalogue says "between consecutive updates", which
is CKS's `e_n`. The paper's headline result is about `OFI_k`, the sum over an interval, and that is
what has the linear price-impact relation. A summed variant is a *different feature* and under the
catalogue's own rule ("a definition change gets a NEW name") would need one. Worth confirming that
the per-update `e_n` is what Phase 4 actually intends to feed the models, because it is not what the
paper regresses on.

---

## Files produced (all in this scratchpad, nothing in the repo)

- `bench_merge.py`, `bench_merge2.py` — Q2 merge variants (a/b/c/c′/d)
- `toy_merge_sorted.py` — `merge_sorted` tie-order pin
- `bench_window.py` — Q3 synthetic 26M benchmark + float64 drift measurement
- `bench_real.py` — Q3 real-day benchmark + CKS OFI kernel and its polars cross-check
- `rank_test.py`, `rank_test2.py` — both-rank-orders equality of all four features at decision rows; int64-vs-float64 ring
- `cks.txt`, `ml_ofi.txt` — `pdftotext` extractions of the two papers
- `nbcache/` — numba cache, deliberately outside the repo
