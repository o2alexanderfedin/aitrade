---
status: resolved
verdict: H1
trigger: "A frozen one-feature Stage-1 model earns +$625.08 over 155,927 trades on five in-sample OOF blocks (44.2M rows). Its 10s forecast skill is tiny (r2_vs_zero 0.006-0.035) but the sign of its prediction agrees with the sign of the NEXT ROW's mid change 91.5%-96.0% (82.5%-94.6% tick-weighted). Decide between H1 (genuine microstructure, no bug) and H2 (an alignment defect / leakage)."
created: 2026-09-29
updated: 2026-09-29
looks_spent_by_this_investigation: 0
---

# Verdict: H1. No bug.

`imb_top` is computed from the book that prevailed **before** the move it
predicts, and the thing it predicts is real: a top of book that has run thin on
one side is about to lose that side. The 91.5-96% is not a lookahead — it is a
statistic conditioned on the 0.34%-1.5% of decision rows whose next row has a
different mid, and on exactly those rows the touch is nearly one-sided (median
`|imb_top|` 0.98-0.997, against 0.50-0.68 on rows where the mid stays put). The
model is a detector of imminent queue depletion, not a 10-second forecaster,
which is why its `r2` against a 10-second label is ~0.02 while its next-row sign
is ~0.95.

**H2 is dead on five independent counts**, three of them decisive on their own:

1. Stored `imb_top` equals the prevailing-quote recomputation from the curated
   book on 10/10 sampled rows, and the next book update would have given a
   different value on 10/10.
2. The full real pipeline (`project -> merge_sorted -> decision_row_index ->
   kernel`) reproduces the cached OOF frame **bit-identically** on a 1.86M-event
   real slice, and truncating that stream at a decision row leaves the prefix
   bit-identical.
3. The forward association is the **maximum** of the whole lead/lag profile on
   every block and every measure; the backward association is the **minimum**.
4. Lagging the prediction by one decision row costs **2.5%** of the P&L. An
   off-by-one whose removal costs 2.5% was not carrying the other 97.5%.
5. The profit degrades gracefully with latency (84% retained at ~20-40 ms), which
   no one-instant artifact does.

**The profit is an artifact of ZERO FEES, not of the fill assumption.** The edge
is 6,250,827 ticks over 155,927 trades = **40.09 ticks per round trip = 0.519 bp**
of a ~$77,000 mid. The viability script's own `x_bps=1` probe already shows what
that means: a 1 bp entry threshold (74-79 ticks) produces **zero trades and zero
P&L on all five blocks**. The model's whole predicted excursion beyond the touch
is under one basis point, so any friction of that order erases it.

Fill and latency are *not* what carry it, and I have numbers against both: a
one-row (~1 ms) lag costs 2.5%, a 20-40 ms lag costs 16%, and the side the model
takes has a median 2.884 BTC resting against its 0.001 BTC order — at or below the
order size on only 0.19% of rows. The unconditional fill and zero latency remain
unregistered simplifications and should be registered, but they are secondary; the
sign of this P&L depends on the zero-fee simplification. None of this is a defect,
and none of it invalidates a number this phase produced.

---

## 1. The discriminating test

For each block: the association between `imb_top[i]` and the mid change at
offset *k*, where the change is `mid[i+k] - mid[i+k-1]`. `back1` is the backward
change `mid[i] - mid[i-1]`; `fwd1` is the forward change `mid[i+1] - mid[i]` —
the one the 91.5-96% statistic is computed on.

Signed Spearman rank correlation, all rows (2M-row subsample where larger):

| block | back3 | back2 | back1 | **fwd1** | fwd2 | fwd3 |
|---|---|---|---|---|---|---|
| oof_block_0 | 0.0739 | 0.0735 | 0.0698 | **0.0791** | 0.0773 | 0.0744 |
| oof_block_1 | 0.1181 | 0.1173 | 0.1141 | **0.1253** | 0.1209 | 0.1171 |
| oof_block_2 | 0.1649 | 0.1660 | 0.1617 | **0.1739** | 0.1689 | 0.1617 |
| oof_block_3 | 0.1675 | 0.1690 | 0.1676 | **0.1886** | 0.1834 | 0.1751 |
| oof_block_4 | 0.1937 | 0.1979 | 0.1962 | **0.2133** | 0.2046 | 0.1969 |

Same, restricted to rows where that change is nonzero:

| block | back3 | back2 | back1 | **fwd1** | fwd2 | fwd3 |
|---|---|---|---|---|---|---|
| oof_block_0 | 0.6496 | 0.6452 | 0.6162 | **0.7026** | 0.6900 | 0.6710 |
| oof_block_1 | 0.6852 | 0.6885 | 0.6650 | **0.7263** | 0.7154 | 0.7050 |
| oof_block_2 | 0.7000 | 0.7071 | 0.6854 | **0.7292** | 0.7132 | 0.7012 |
| oof_block_3 | 0.6666 | 0.6723 | 0.6536 | **0.7167** | 0.6956 | 0.6789 |
| oof_block_4 | 0.6949 | 0.7039 | 0.6879 | **0.7274** | 0.7080 | 0.6941 |

Sign-agreement rate, nonzero changes:

| block | back3 | back2 | back1 | **fwd1** | fwd2 | fwd3 |
|---|---|---|---|---|---|---|
| oof_block_0 | 0.9206 | 0.9103 | 0.8794 | **0.9150** | 0.9121 | 0.9053 |
| oof_block_1 | 0.9510 | 0.9453 | 0.9251 | **0.9533** | 0.9541 | 0.9505 |
| oof_block_2 | 0.9594 | 0.9548 | 0.9394 | **0.9604** | 0.9580 | 0.9530 |
| oof_block_3 | 0.9206 | 0.9156 | 0.8948 | **0.9391** | 0.9383 | 0.9312 |
| oof_block_4 | 0.9456 | 0.9427 | 0.9282 | **0.9548** | 0.9536 | 0.9489 |

**`fwd1` is the maximum and `back1` is the minimum, on all five blocks, on all
three measures.** The H2 signature — backward materially stronger than forward —
does not appear. The profile is a smooth peak at the forward offset decaying in
both directions, which is what a persistent state variable that genuinely leads
the price looks like.

The whole profile is high because the state is persistent: `back3` already sits
at 0.65-0.70 Spearman. That is the shape of a slowly-varying book imbalance, not
of a one-row misalignment, which would put a spike at exactly one offset and
leave the neighbours at baseline.

### The one place the H2 signature does appear, and why it is not H2

On `oof_block_0` the **tick-weighted** agreement inverts: back1/back2/back3 =
0.873/0.921/0.928 against fwd1 = 0.823. Decomposed by move size, the inversion
lives entirely in the tail:

| oof_block_0 bucket | n | fwd unwt | fwd tick-wt | bak unwt | bak tick-wt |
|---|---|---|---|---|---|
| <=1 tick | 3851 | 0.9057 | 0.9232 | 0.8878 | 0.9028 |
| 1-3 ticks | 2385 | 0.9208 | 0.9252 | 0.8793 | 0.8804 |
| 3-10 ticks | 3875 | 0.9285 | 0.9334 | 0.8833 | 0.8873 |
| 10-50 ticks | 3550 | 0.9445 | 0.9412 | 0.8707 | 0.8617 |
| 50-200 ticks | 522 | 0.6820 | 0.6269 | 0.8429 | 0.8698 |
| >200 ticks | **18** | 0.1667 | 0.1645 | 1.0000 | 1.0000 |

Forward beats backward in every bucket up to 50 ticks. Above 50 ticks forward
**collapses** and backward goes to 1.000. `oof_block_1` and `oof_block_3` show
the identical pattern (forward dominant to 50 ticks, inverted above it, n=17 and
n=170 in the top bucket). A genuine off-by-one would invert *uniformly across
every bucket*; this inverts only where the sample is 18 moves.

The physics is straightforward and is itself a finding: `imb_top` does **not**
predict large dislocations (0.167 forward agreement on block 0's 18 biggest
moves — worse than a coin), and after a large dislocation the book is left fully
one-sided in the direction it moved (1.000 backward agreement). Block 0's
tick-weighted statistic is a thin-tailed number — its top 1% of moves carry 14%
of total variation across only 143 moves — so the tail governs it. The four dense
blocks are forward-dominant tick-weighted too (0.928/0.946/0.927/0.945 vs
0.888/0.913/0.850/0.904 backward).

This also disposes of `frozen_lead1` gaining +22.6% on `oof_block_0` — the one
number a hostile reader would cite for H2. It is the same tail: block 0 is the
sparse day with 3,566 trades and 70.2% of its targets exactly zero, and a handful
of large moves decide its P&L. `lead10` gains less (+15.8%) than `lead1`, so even
on block 0 the profile is not monotone in lookahead; and on the four dense blocks
a lead **loses** 2%-12%. An alignment defect places its peak at the same offset
on every block. This one does not.

## 2. Reconstructing `imb_top` from source

Ten decision rows from `oof_block_1` (2026-09-13) — eight drawn from the rows
whose next mid differs (the population the 95% is computed on) and two on trade
decision rows, where the prevailing quote is genuinely older than `t`. For each:
the last curated bookTicker row with `etime <= etime_i` in `(etime, seq)` order,
`(bid_qty - ask_qty)/(bid_qty + ask_qty)` recomputed by hand.

| row | etime | rank | stored imb | recomputed | match | bid_qty | ask_qty | next update's imb | next-row dmid (ticks) |
|---|---|---|---|---|---|---|---|---|---|
| 2296639 | 1789292322046000000 | 0 | 0.999912 | 0.999912 | OK | 22.618 | 0.001 | 0.999912 | +23.0 |
| 5071621 | 1789322099165000000 | 1 | -0.768274 | -0.768274 | OK | 0.764 | 5.830 | -0.789991 | -8.5 |
| 5681965 | 1789332622858000000 | 1 | 0.695409 | 0.695409 | OK | 15.474 | 2.780 | 0.743353 | +9.0 |
| 5930753 | 1789337078005000000 | 0 | -0.999905 | -0.999905 | OK | 0.001 | 21.080 | -0.999906 | -6.0 |
| 6100024 | 1789337820764000000 | 0 | 0.998829 | 0.998829 | OK | 3.414 | 0.002 | 0.998830 | +45.0 |
| 6258083 | 1789338757846000000 | 0 | -0.998388 | -0.998388 | OK | 0.002 | 2.479 | -0.998488 | -7.5 |
| 6453739 | 1789340295382000000 | 0 | 0.716844 | 0.716844 | OK | 4.026 | 0.664 | 0.716965 | +7.0 |
| 6655443 | 1789341863295000000 | 1 | 0.943128 | 0.943128 | OK | 0.820 | 0.024 | 0.966410 | +15.0 |
| 1290218 | 1789281038926000000 | 1 | -0.672431 | -0.672431 | OK | 2.292 | 11.702 | -0.673341 | +0.0 |
| 5739827 | 1789334018949000000 | 1 | 0.868181 | 0.868181 | OK | 14.555 | 1.027 | 0.869021 | +0.0 |

- `mid` matches the prevailing-quote recomputation: **10/10** (exact float equality)
- `imb_top` matches: **10/10** (exact float equality)
- the **next** bookTicker update would have given a different `imb_top`: **10/10**

The stored value is the pre-move one. Note rows 2296639 and 5930753: one side
down to 0.001 BTC — a single minimum lot — and the mid then moves 23 and 6 ticks
in the direction the thin side implies.

The depletion is visible directly in the raw curated stream around row 5681965.
Thirteen consecutive bookTicker updates, seven of them sharing one `etime`
(ms-resolution clock, ~7 updates per ms):

```
 off                etime        bid      bid_qty        ask      ask_qty        mid    imb_top
  -6  1789332622858000000    77300.4       13.745    77300.5        3.650     77300.45   0.580339
  -5  1789332622858000000    77300.4       13.745    77300.5        2.988     77300.45   0.642861
  -4  1789332622858000000    77300.4       14.061    77300.5        2.988     77300.45   0.649481
  -3  1789332622858000000    77300.4       14.413    77300.5        2.988     77300.45   0.656571
  -2  1789332622858000000    77300.4       14.942    77300.5        2.988     77300.45   0.666704
  -1  1789332622858000000    77300.4       14.942    77300.5        2.780     77300.45   0.686266
   0  1789332622858000000    77300.4       15.474    77300.5        2.780     77300.45   0.695409  <== prevailing quote for the decision row
   1  1789332622859000000    77300.4       15.474    77300.5        2.278     77300.45   0.743353
   2  1789332622859000000    77300.4       15.474    77300.5        2.137     77300.45   0.757311
   3  1789332622859000000    77300.4       15.474    77300.5        2.077     77300.45   0.763318
   4  1789332622859000000    77300.4       15.474    77300.5        1.994     77300.45   0.771697
   5  1789332622859000000    77300.4       16.037    77300.5        1.994     77300.45   0.778825
   6  1789332622859000000    77300.4       16.037    77300.5        1.994     77300.45   0.778825
```

The ask is being eaten (3.650 -> 1.994) while the bid grows (13.745 -> 16.037).
`imb_top` climbs monotonically. Nine ticks later the mid is higher. Nothing in
row 0's value comes from after row 0.

Because ~7 book updates share one millisecond and the decision row is the last
of each `etime` run, `mid[i+1] - mid[i]` is **not** "the next book update" — it
is the book roughly 1 ms and several updates later. `fwd1` is a genuine ~1 ms
forward horizon.

### Why the mid moves 5-10 ticks between consecutive decision rows

A side observation worth recording, because it looks wrong until it is
explained. Median nonzero move is 4.0-6.5 ticks and 78-83% of total variation
comes from moves >= 10 ticks, while the mean spread is only 1.03-1.12 ticks.
These are consistent: the touch is tight almost always, but thin, and when it
clears the next populated level is several ticks away. This is **not** a data-gap
artifact — the median `etime` gap between consecutive decision rows is 1-2 ms
*including* the big moves, and pairs spanning >1 s account for 0.00%-0.19% of
total variation.

## 3. The label's information set

The 10-second target at row *i* reads the **prevailing** mid at `etime_i + 10e9`
ns — `np.searchsorted(quote_etime, t+h, side="right") - 1` over the combined
D + D+1 curated quote array (`features/labels.py:334`), value computed as
`(quote_mid[idx] - decision_mid) / decision_mid`.

Independently recomputed for 12 sampled labelled rows of `oof_block_1` from the
curated 2026-09-13 + 2026-09-14 bookTicker partitions:

- recomputed == stored, **exact float equality: 12/12**
- quotes used with `etime > t+h` (which would be a leak): **0/12**
- quotes used with `etime > t` (genuinely in the forward window): **12/12**
- perturbing all 55,320,712 quotes with `etime > t+h` by +1000: label **UNCHANGED**
- perturbing the quote at the prevailing `t+h` position: label **CHANGED** (anti-vacuity)

The null mask additionally reads quote *arrival times* to `t + h + max_quote_gap`
(`null_gap`) and compares `idx <= idx_at_t` (`null_stale`). Both can only ever
remove a label, never alter a finite one — documented in the module header, and
consistent with what I measured.

## 4. What the simulator's fill row actually is

From `git show HEAD:mvp/sim/kernel.py` (snapshotted to avoid the concurrent
edit). The loop is index-aligned with no shift anywhere:

```
p = pred[i]                             # the prediction AT row i
b = bid_ticks[i];  a = ask_ticks[i]     # row i's OWN touch
long_trigger  = pred_ticks_floor > a + x_ticks
short_trigger = pred_ticks_ceil  < b - x_ticks
...  fill_price_ticks = a               # long fills at row i's ask
...  fill_price_ticks = b               # short fills at row i's bid
```

`neutral_fill_null_predictions` is elementwise (`mid * (1 + pred_return)`) and
introduces no offset.

So the decision and the fill are simultaneous, at the row's own quote, using the
row's own feature. Both sides of that come from information set `t`.
**This is the documented zero-latency simplification, not a defect.** The bug
H2 posited would be a feature computed from a *later* book state, and section 2
shows it is not.

Two things *are* unregistered simplifications here. The fill is
**unconditional** — the kernel fills whenever the trigger fires, with no queue
position and no adverse selection — and latency is zero. Both should be
registered. But neither is what carries the P&L, and I have measurements against
both.

On the 2 h real slice (749,932 decision rows), the side the model takes (the ask
when `imb_top > 0`, the bid when `imb_top < 0`) has a **median 2.884 BTC
resting** against a 0.001 BTC order, and rests at or below the order size on only
**0.19%** of rows. The untaken side's median is 14.996 BTC (5.2x). So the typical
fill is *not* against a vanishing queue — I expected it would be and it is not.
Latency degrades the P&L gracefully (section 7: 84% retained at 20-40 ms).

What the sign of the P&L actually depends on is **zero fees**. The realised edge
is 40.09 ticks per round trip (6,250,827 / 155,927), which is **0.519 bp** of a
~$77,000 mid. The viability script's `x_bps=1` probe is already in the evidence
file and settles it:

| block | x=0 ticks | x=0 trades | x=1 ticks | x=1 trades | x_ticks at 1 bp |
|---|---|---|---|---|---|
| oof_block_0 | 96,714 | 3,566 | **0** | **0** | 77 |
| oof_block_1 | 423,240 | 10,673 | **0** | **0** | 76-77 |
| oof_block_2 | 1,469,367 | 32,241 | **0** | **0** | 76-79 |
| oof_block_3 | 2,257,251 | 61,970 | **0** | **0** | 74-78 |
| oof_block_4 | 2,004,255 | 47,477 | **0** | **0** | 75-76 |
| TOTAL | 6,250,827 | 155,927 | **0** | **0** | |

One basis point of required overshoot beyond the touch produces zero trades on
every block. The model's entire predicted excursion lives inside one tick of
significance. **This does not settle whether the P&L survives a fill model** —
that is out of scope — but it does identify which simplification the sign hangs
on, and it is not the fill.

## 5. Re-deriving the CI leakage proof rather than trusting it green

The proof that covers `imb_top` is
`tests/leakage/test_feature_information_set.py`. It is green (11 passed, 1.09 s)
and — unlike the case this phase already hit — it does prove the advertised
proposition. Three properties, and the third is the one that matters:

- `test_future_deletion_cannot_change_a_feature_at_t` truncates an arbitrary
  suffix and compares the surviving prefix bitwise. That is the causal claim.
- `test_feature_measured_lookback_matches_the_declaration` perturbs **every**
  row one field at a time *including rows after the decision row*
  (`_measured_sources(include_future=True)`) and asserts no row `j > k` can move
  a feature at `k`.
- `test_a_feature_IS_sensitive_to_its_own_declared_window` is the anti-vacuity
  guard, asserting `i1 in measured["imb_top"][k]` — so the invariance above is
  not passing on a constant.

**What it does not cover, and why I re-derived it.** All of it runs against
`features.reference` on a 17-row crafted stream and hypothesis streams of <= 40
rows. Nothing goes through `merge_curated_streams` (the real `merge_sorted` tie
order), `features/build.py`'s chunking, the partition write/read, or the
accessor's segment slice into the cached frame. The kernel is structurally causal
by inspection — `features/kernel.py` updates `prev_bid_*`/`prev_ask_*` from row
*i*'s own payload and then emits, with no forward reference anywhere — so the
only remaining leak vectors are upstream of it, and they are exactly what the
shipped proof omits.

Re-derived on real data through the full path, 2026-09-13 12:00-14:00 UTC
(1,737,962 bookTicker + 119,291 trade rows -> 1,857,253 merged events ->
749,932 decision rows):

**(A) does the full real path reproduce the cached OOF frame?**

| check | result |
|---|---|
| cached decision rows in window / mine / joined on etime | 749,932 / 749,932 / 749,932 |
| `mid` bit-identical | **True** |
| `imb_top` bit-identical | **True** |
| `decision_source_rank` identical | **True** |

**(B) delete every event after decision row k:**

| cut | k | decision rows re-derived identically | prefix bit-identical (mid, imb_top, ofi, trade_flow) |
|---|---|---|---|
| 25% | 382,889 | True | True, True, True, True |
| 50% | 869,845 | True | True, True, True, True |
| 75% | 1,402,252 | True | True, True, True, True |

**(C) permute every event payload after k** (k=869,846; 987,406 rows permuted):
prefix bit-identical on all four features — **and the permutation did change the
tail**, so the invariance is not vacuous.

## 6. A null for the 91.5-96%

Sign agreement against the forward change, same `moved` conditioning as the
original statistic:

| predictor | blk0 | blk1 | blk2 | blk3 | blk4 |
|---|---|---|---|---|---|
| `imb_top` (the model) | 0.9150 | 0.9533 | 0.9604 | 0.9391 | 0.9548 |
| `imb_top` **lagged 1 row** | 0.9121 | **0.9541** | 0.9580 | 0.9383 | 0.9536 |
| `imb_top` lagged 10 rows | 0.8545 | 0.9177 | 0.9161 | 0.8738 | 0.9087 |
| `ofi` (a different real feature) | 0.8207 | 0.8215 | 0.8343 | 0.8151 | 0.8369 |
| seeded random +-1 | 0.5015 | 0.5006 | 0.4990 | 0.5003 | 0.4996 |
| constant +1 | 0.5087 | 0.4953 | 0.5024 | 0.4976 | 0.4978 |

Lagging by one row does not dent it at all — on `oof_block_1` it goes *up*.
Ten rows costs 3-6 points. Random is 0.50, so the rate is real; but by the
question's own criterion, **the signal is about persistent book state, not about
the specific instant.** An off-by-one cannot be the mechanism of something that
survives being shifted by one.

Is 0.95 remarkable? No. It is a statistic conditioned on the 0.34%-1.5% of rows
whose next row has a different mid, and those are exactly the rows where the book
is one-sided:

| block | rows whose next mid moves | median abs imb_top there | on all other rows | frac > 0.99 there | on all other rows |
|---|---|---|---|---|---|
| oof_block_0 | 14,204 / 4,137,073 | 0.9824 | 0.5019 | 0.445 | 0.007 |
| oof_block_1 | 50,959 / 6,864,853 | 0.9960 | 0.6352 | 0.592 | 0.026 |
| oof_block_2 | 165,735 / 11,291,365 | 0.9975 | 0.6800 | 0.641 | 0.051 |
| oof_block_3 | 229,122 / 12,142,146 | 0.9951 | 0.6141 | 0.570 | 0.033 |
| oof_block_4 | 218,385 / 9,814,111 | 0.9973 | 0.6398 | 0.641 | 0.051 |

On 44-64% of the rows that precede a move, one side of the book is under 0.5% of
the other. Saying "the mid will go up" there is close to reading the outcome off
the queue. That is H1, stated plainly.

## 7. The lag/lead profile in full, and what it means for latency

`closed_pnl_ticks` at `x_bps=0`, from
`evidence/07-oof-viability-results.json` (not re-run — the viability script
imports `sim/kernel.py` and `models/gates.py`, both under concurrent edit):

| variant | blk0 | blk1 | blk2 | blk3 | blk4 | TOTAL | vs frozen |
|---|---|---|---|---|---|---|---|
| frozen | 96,714 | 423,240 | 1,469,367 | 2,257,251 | 2,004,255 | 6,250,827 | — |
| frozen_lag1 | 93,964 | 416,034 | 1,437,079 | 2,183,739 | 1,964,529 | 6,095,345 | -2.5% |
| frozen_lag10 | 77,122 | 378,144 | 1,286,739 | 1,762,183 | 1,729,015 | 5,233,203 | -16.3% |
| frozen_lag100 | 33,410 | 218,080 | 731,840 | 650,877 | 662,219 | 2,296,426 | -63.3% |
| frozen_lead1 | 118,608 | 400,262 | 1,408,837 | 1,982,975 | 1,889,643 | 5,800,325 | -7.2% |
| frozen_lead10 | 111,992 | 412,314 | 1,432,763 | 1,902,751 | 1,834,495 | 5,694,315 | -8.9% |
| frozen_negated | -108,478 | -453,938 | -1,560,247 | -2,438,873 | -2,139,275 | -6,700,811 | mirror |
| frozen_shuffled | -1,894,165 | -3,200,232 | -5,456,416 | -5,710,954 | -4,716,457 | -20,978,224 | -1.03 ticks/trade over 20.4M trades |
| zero_predictor | 0 | 0 | 0 | 0 | 0 | 0 | — |

**The lag test alone is confounded and I am not resting the verdict on it.**
Under H1, lag0 > lag1 because the feature goes stale. Under H2, lag0 > lag1
because the leak is removed. Both predict the same ordering. What the *magnitude*
says is decisive though: if lag0 were leaked and lag1 the honest alignment, then
the honest version earns 97.5% of the leaked one — so the leak would be
responsible for 2.5% of the P&L, not the P&L.

Translated to elapsed time (median `etime[i+k] - etime[i]`):

| lag | blk0 | blk1 | blk2 | blk3 | blk4 | P&L retained |
|---|---|---|---|---|---|---|
| 1 row | 2 ms | 1 ms | 1 ms | 1 ms | 1 ms | 97.5% |
| 10 rows | 19 ms | 39 ms | 27 ms | 21 ms | 29 ms | 83.7% |
| 100 rows | 220 ms | 724 ms | 455 ms | 330 ms | 444 ms | 36.7% |

A realistic 20-40 ms round trip retains ~84% of the P&L. No lookahead artifact
behaves that way. `frozen_negated` is the exact mirror, so the sign carries the
money rather than the flip rule; `frozen_shuffled` loses ~1.03 ticks per trade
over 20.4M trades, which is the spread, so alignment (not amplitude) is what
earns.

---

## What becomes invalid

**Nothing.** No leakage was found. The five OOF looks stand, the frozen
predictor's `imb_top` values are the pre-move book state, and the 10-second
labels read nothing after `t+h`.

The +$625.08 remains what the viability script already declared it to be — in
sample twice over (these five blocks selected the winner; the frozen body was
fitted on a train cache containing all five) — and is now also known to be
**0.519 bp per round trip, positive only under zero fees**: the existing
`x_bps=1` probe produces zero trades on all five blocks. Unconditional fill and
zero latency should also be registered, but the measurements above show they are
not what the sign depends on.

## In bold: what I did NOT check

- **The `val` segment, in any way.** Zero looks spent; `val` = 0 before and after.
- **Whether the P&L survives a fill model.** Out of scope by instruction. I
  measured resting size on the taken side (median 2.884 BTC vs a 0.001 BTC order)
  and latency decay (84% at 20-40 ms), which bound the objection but do not
  answer it. Queue position, partial fills, and adverse selection against a
  quote that is *about to be taken by someone else* are untested.
- **The venue's actual fee schedule.** I did not look it up. The statement I
  stand behind is the measured one: the edge is 0.519 bp per round trip and a
  1 bp threshold zeroes it. Whether Binance USDS-M taker fees are 2 bp or 5 bp
  per side changes the margin, not the conclusion, but I did not verify them.
- **Whether curated `etime` is the right clock.** I took `etime` as given
  (Phase 3's question). If the exchange `E`/`T` field were mis-assigned at
  ingest, every statement here inherits that error. What I *did* close: the
  within-millisecond ordering. `etime` has ms resolution and ~7 book updates
  share one value, so the order rests on `seq` — and over all 17,167,290
  bookTicker rows of 2026-09-13, sorting by `(etime, seq)` leaves the venue's own
  `update_id` **strictly increasing, zero violations**. The reconstruction's
  "last update at or before t" is therefore the venue's last update too, not an
  artifact of the ingest's row numbering.
- **`features/build.py`'s chunking and the partition writer.** I re-derived the
  path `project -> merge -> decision_row_index -> kernel` on real data, and
  confirmed it reproduces the cached frame. I did **not** exercise `build.py`'s
  chunk-boundary resumption or the `features_norm` normalization artifact. The
  bit-identical agreement in section 5(A) makes a defect there unlikely for `mid`
  and `imb_top` on this slice, but it is an inference, not a test.
- **Only a 2-hour slice of one day** carried the full-path re-derivation
  (1.86M of 44.2M events). Blocks 0, 2, 3, 4 were checked only via the cached
  frames.
- **`ofi` and `trade_flow` alignment beyond the truncation/shuffle checks.**
  Their coefficients are exactly 0.0 in the frozen body, so they cannot affect
  this P&L, and I did not reconstruct them from source.
- **The normalization step.** I worked from raw `imb_top` throughout; the model
  scores a normalized column. Normalization is affine with positive scale, so
  signs and rank correlations are unaffected — but I did not read the
  normalization artifact to confirm the scale is positive.
- **The 10 reconstructed rows are 10 rows.** Exact on all of them, and section
  5(A) generalises the same claim to 749,932 rows through a different route, but
  no exhaustive per-row reconstruction against the curated book was run over a
  whole block.

---

## Session bookkeeping

Look counters, all 16 (2 committed segment manifests x 8 segment names):

| manifest | train | val | held_out | oof_0 | oof_1 | oof_2 | oof_3 | oof_4 |
|---|---|---|---|---|---|---|---|---|
| 807125015b25... | 0 | **0** | 0 | 1 | 1 | 1 | 1 | 1 |
| 97964cb27f62... | 0 | **0** | 0 | 0 | 0 | 0 | 0 | 0 |

Total = 5, `val` = 0. Identical before the first command and after the last.
Nothing in the lake, the registry or the MLflow store was written; no run was
opened and no tag set. The cached OOF frames at
`/Volumes/ProjectsSSD/aihedgefund/scratch/phase07/` were read only.
