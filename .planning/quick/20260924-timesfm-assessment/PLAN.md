---
type: assessment
slug: timesfm-assessment
status: complete
created: 2026-09-24
decision: not-now
---

# Should TimesFM forecast for this project?

Assessment requested mid-Phase-7. No code written; the deliverable is a
decision plus two recorded experiments. **Verdict: not in this milestone, and
not for the 10-second return — but one variant is worth doing in Phase 8 and one
post-MVP.**

## The decisive finding: the capable version is licence-locked

The proposal was to feed several correlated streams at once (BTC + ETH + USDT +
news embeddings per record) and let the model find the cross-dependencies.

| | TimesFM 2.5 | TimesFM 3.0 (2026-08-31) |
|---|---|---|
| Weights licence | Apache-2.0 | `timesfm-non-commercial-license-v1.0` — **non-commercial, non-production** |
| Multiple series jointly | **No** — univariate only | **Yes** — first TimesFM trained natively multivariate |
| Covariates | XReg: a ridge regression fitted OUTSIDE the model on the in-context window; TimesFM then forecasts the residuals | past-only and past+future covariates natively |
| Params / context | 200M / 16,384 | 330M / 16,384, horizon to 1,024 |

So the version that does what was asked forbids the use, and the version that may
be used cannot do what was asked. Under 2.5, "give it parallel streams" means
hand-building a linear cross-asset model yourself and letting TimesFM tidy the
leftovers — the cross-asset structure would be YOUR ridge, not the foundation
model's.

**Sourcing honesty:** read from the repo README and secondary coverage. The
`LICENSE-MODEL-3.0` text itself returned 404 at the path tried and has NOT been
read. Before any commitment to 3.0 weights, read the licence file itself.

## The deeper objection, independent of licence: the target

The forecast target is the 10-second midprice return. Three numbers this project
has already measured make the mismatch concrete:

- **43.9%** of 10s returns are EXACTLY zero.
- A perfect-foresight oracle extracts **$29.46 on a whole real day** at the $100
  position cap. One tick (0.1 USDT on a ~$77k price) is 1.3 bp and the spread
  eats most of it.
- Decision rows are **event-time and irregular**, ~6.8M per day.

TimesFM needs a regularly-spaced series at a declared frequency, so feeding it
means resampling. At 1s bars the geometry actually works (86,400 points/day; a
16,384 context is ~4.5 hours of history to predict 10 steps ahead) — but 1s bars
destroy precisely what this project's features encode. `ofi`, `imb_top` and
`trade_flow` are event-driven; a 1s bar is a lossy summary of ~80 events.

**And the failure mode is seductive rather than obvious.** A foundation model's
prior is "continue the pattern". On a near-martingale price series that prior
collapses to last-value persistence, which scores beautifully on MAPE/MASE
against the PRICE and exactly zero against the RETURN. This project is unusually
well defended against that particular self-deception: D-07-18 scores R² against
a **constant-zero** predictor rather than against the target mean, and D-07-19
raises on any P&L at or above the ceiling. Both would catch it on the first run.

## Where the instinct is right: forecast volatility, not return

Returns at 10s are close to unpredictable by construction — any exploitable
autocorrelation gets arbitraged away. **Volatility is the opposite**: volatility
clustering is one of the most robust regularities in market data, which is the
entire reason the GARCH/HAR literature exists. That makes a realised-volatility
forecast over the next 1–10 minutes a target where a pretrained long-context
prior can genuinely earn its keep, and:

- the model's 9-quantile output is exactly the right shape for it;
- it is **directly monetizable inside the Stage 2 this project already plans**:
  the TOB-cross threshold X is currently a fixed constant, and making it
  state-dependent on predicted volatility — wider when a violent minute is
  coming, tighter when quiet — is exactly the class of improvement Phase 10's
  agentic loop exists to produce;
- the project already carries `ret_1min_mid` and `ret_10min_mid` as diagnostic
  labels, so the longer horizon is already catalogued.

## On the three proposed streams, individually

- **ETH alongside BTC** — real. Cross-asset lead-lag at high frequency is well
  documented. But the right mechanism is the **Phase 8 PyTorch transformer**,
  where extra channels are a natural input and the causal-masking leakage test
  already exists in CI. Adding ETH is a CAPTURE decision (the daemon records
  BTCUSDT only) long before it is a model decision — and capture is currently
  stopped.
- **USDT** — it is pegged to $1. As a routine predictor channel it is a
  near-constant column carrying almost no information; it matters only during
  depeg events, which are rare and regime-breaking. Its honest use is a regime
  FLAG (`|USDT − 1| > threshold`), not a forecast input.
- **News embeddings** — there is no news pipeline in this project at all; capture
  is `bookTicker` + `trade`. Feeding a 768-dim embedding through 2.5's LINEAR
  XReg is a very weak use of it. It also opens a leakage hole the current
  methodology cannot audit, because the embedding model has its own corpus and
  its own cutoff.

## The objection that matters most here: auditability

This project's stated core value is a reproducible, **leakage-proof** pipeline
with per-feature information-set proofs in CI, embargo ≥ label horizon, and a
one-look lockbox. A pretrained model's weights are an un-auditable information
set — you cannot write an information-set proof for "what did these 330M
parameters already see".

Stated fairly, the mitigation: the evaluation window is 2026-09-12..18 and
TimesFM 3.0 was released 2026-08-31, so its corpus predates this data. A side
experiment on THIS window is safe on cutoff grounds and genuinely auditable.
What it cannot be, under the current CI rules, is a catalogued feature — that
would need a new kind of proof that does not exist yet.

## Two recorded experiments

**E1 — Phase 8, cheap, as a baseline to BEAT (not a model to ship).**
Zero-shot TimesFM 2.5 (Apache-2.0 weights, no licence issue for a baseline) on
1s-resampled mid, scored on the RETURN against constant-zero, on the same folds
as the three sanctioned classes. If ridge, LightGBM and the transformer cannot
beat a zero-shot foundation model at 10s, that is worth knowing BEFORE the v0
gate rather than after. Cost: one dependency and a weights download — both
forbidden in the current session, so it belongs to Phase 8's discuss.

**E2 — post-MVP, the promising one.**
Realised-volatility quantile forecasting at 1–10 min to make Stage 2's X
state-dependent. Requires a licence resolution if it uses 3.0 weights.

## Why not now

- The current session is explicitly constrained to no downloading and no
  producing massive data; the weights alone are several hundred MB.
- Phase 7's whole purpose is to prove the plumbing with the CHEAPEST model class.
  Introducing a 330M-parameter foundation model into the phase that exists to
  de-risk the Trainer protocol inverts the point of the phase.
- Phase 8's GPU question (mvp.md Q3) is still unresolved and already blocks the
  transformer track. Adding a second GPU-hungry class compounds an open blocker.

## Not done

- **The 3.0 licence file was not read** (404 on the path tried). The commercial
  restriction is reported from the repo README and secondary coverage, and must
  be confirmed against the licence text before any decision that depends on it.
- No benchmark was run; no claim here rests on measured TimesFM accuracy on this
  project's data. E1 exists precisely because that number is unknown.
- ETH/USDT/news availability was not investigated beyond noting that capture
  records neither.
