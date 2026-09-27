---
type: assessment
slug: timesfm-assessment
status: complete
created: 2026-09-24
decision: not-now
---

# Should TimesFM forecast for this project?

Assessment requested mid-Phase-7. No code written; the deliverable is a decision
plus two recorded experiments. **Verdict: yes eventually, no for the 10-second
return, and not in this phase.** Use **TimesFM 3.0** — it is the first version
trained natively multivariate, which is exactly what the proposal asked for.

## The version matters, because only one of them does what was asked

The proposal was to feed several correlated streams at once (BTC + ETH + USDT +
news embeddings per record) and let the model find the cross-dependencies.

| | TimesFM 2.5 | TimesFM 3.0 |
|---|---|---|
| Multiple series jointly | **No** — univariate only | **Yes** — first TimesFM trained natively multivariate |
| Covariates | XReg: a ridge fitted OUTSIDE the model on the in-context window; TimesFM forecasts the residuals | past-only and past+future covariates natively |
| Params / context / horizon | 200M / 16,384 | 330M / 16,384 / up to 1,024 |
| Quantiles | yes | 9 quantiles (deciles) |

Under 2.5 the cross-asset structure would have been our own ridge with the
foundation model merely tidying residuals. Under 3.0 it is the model's own job.
So the idea is real — on 3.0.

## The real objection: the target, not the tool

The forecast target is the 10-second midprice return. Three measured numbers make
the mismatch concrete:

- Exact-zero mass in the target: **20.0% across the pool**, 9.8%–13.8% on the
  candidate validation window (43.9% is 2026-09-13 alone, not representative).
- A perfect-foresight oracle extracts **$112.05 over the two-day validation
  window** at the $100 position cap. One tick (0.1 USDT on ~$77k) is 1.3 bp and
  the spread eats most of it.
- Decision rows are **event-time and irregular**, ~6.8M per day, `etime` unique.

TimesFM needs a regularly-spaced series at a declared frequency, so feeding it
means resampling. At 1s bars the geometry works — 86,400 points/day, and a 16,384
context is ~4.5 hours of history to predict 10 steps ahead — but 1s bars discard
precisely what this project's features encode. `ofi`, `imb_top` and `trade_flow`
are event-driven; a 1s bar is a lossy summary of ~80 events.

**And the failure mode is seductive rather than obvious.** A foundation model's
prior is "continue the pattern". On a near-martingale price series that prior
collapses to last-value persistence, which scores beautifully on MAPE/MASE
against the PRICE and exactly zero against the RETURN. This project is unusually
well defended against that specific self-deception: D-07-32 scores R² against a
constant-zero predictor AND against the target mean, because a constant at the
train mean was measured scoring R²_vs_zero = +0.001647 with zero skill; and
D-07-19 raises on any P&L at or above the ceiling. Both would catch it on the
first run.

## Where the instinct is right: forecast volatility, not return

Returns at 10s are close to unpredictable by construction — any exploitable
autocorrelation gets arbitraged away. **Volatility is the opposite**: volatility
clustering is one of the most robust regularities in market data, which is why the
GARCH/HAR literature exists at all. That makes realised volatility over the next
1–10 minutes a target where a pretrained long-context prior can genuinely earn its
keep, and:

- the 9-quantile output is exactly the right shape for it;
- it is **directly monetizable inside the Stage 2 already planned**: the TOB-cross
  threshold X is currently a fixed constant, and making it state-dependent on
  predicted volatility — wider before a violent minute, tighter in a quiet one —
  is exactly the class of improvement Phase 10's agentic loop exists to produce;
- `ret_1min_mid` and `ret_10min_mid` are already catalogued diagnostic labels, so
  the longer horizon needs no new label work.

## On the three proposed streams, individually

- **ETH alongside BTC** — real. Cross-asset lead-lag at high frequency is well
  documented, and 3.0 can use it jointly. But it is a **capture** decision before
  it is a model decision: the daemon records BTCUSDT only, and capture is
  currently stopped.
- **USDT** — pegged to $1. As a routine channel it is a near-constant column
  carrying almost no information; it matters only during depeg events, which are
  rare and regime-breaking. Its honest use is a regime FLAG
  (`|USDT − 1| > threshold`), not a forecast input.
- **News embeddings** — there is no news pipeline in this project; capture is
  `bookTicker` + `trade`. Worth building only after the numeric channels earn it,
  and it brings its own auditability problem (see below).

## The auditability problem, which is this project's own standard

The stated core value is a reproducible, **leakage-proof** pipeline with
per-feature information-set proofs in CI, embargo ≥ label horizon, and a one-look
lockbox. A pretrained model's weights are an un-auditable information set — no
proof can be written for "what did these 330M parameters already see".

The mitigation that makes a side experiment legitimate: the evaluation window is
2026-09-12..18 and 3.0 shipped 2026-08-31, so its corpus predates this data and a
baseline run is auditable on cutoff grounds. What it cannot be, under current CI
rules, is a **catalogued feature** — that needs a kind of proof that does not exist
yet, and inventing it is its own piece of work.

## Two recorded experiments

**E1 — Phase 8, cheap, as a baseline to BEAT (not a model to ship).**
Zero-shot TimesFM 3.0 on 1s-resampled mid, scored on the RETURN against
constant-zero and against the mean, on the same folds as the three sanctioned
classes. If ridge, LightGBM and the transformer cannot beat a zero-shot
foundation model at 10s, that is worth knowing BEFORE the v0 gate rather than
after. Cost: one dependency plus a weights download, both forbidden in the
current session, so it belongs to Phase 8's discuss.

**E2 — post-MVP, the promising one.**
Realised-volatility quantile forecasting at 1–10 min, BTC and ETH as joint
channels, to make Stage 2's threshold X state-dependent.

## On training our own

The cheapest path is already in the roadmap and needs no separate effort:
**Phase 8's PyTorch transformer track is the seed.** It already has a GPU budget,
a causal-masking leakage test in CI, and a fold harness that owns time. Growing it
— more channels (ETH), longer context, a quantile head instead of a point head —
reaches the same destination through infrastructure that already passes this
project's leakage proofs. A parallel foundation-model effort would duplicate the
transformer track and inherit none of its guarantees.

## Why not now

- The current session is constrained to no downloading and no producing massive
  data; the weights alone are several hundred MB.
- Phase 7 exists to prove the plumbing with the CHEAPEST model class. Introducing
  a 330M-parameter model into the phase that de-risks the Trainer protocol
  inverts the point of the phase.
- Phase 8's GPU question (mvp.md Q3) is unresolved and already blocks the
  transformer track. A second GPU-hungry class compounds an open blocker.

## Not done

- **No benchmark was run.** No claim here rests on measured TimesFM accuracy on
  this project's data — E1 exists precisely because that number is unknown.
- **No weights downloaded, nothing installed.**
- ETH/USDT/news availability was not investigated beyond noting that capture
  records none of them.
