# aitrade

A general two-stage **prediction + monetization platform**: Stage 1 forecasts a target signal from event-stream data; Stage 2 fits a monetization policy on those forecasts in simulation. The methodology core — leakage-proof feature/label catalogues, walk-forward evaluation that owns time, selection-bias budgets, held-out lockbox, parallel model-class tracks — is domain-neutral: it applies to any money market, stock exchange, or non-price domains such as news-landscape prediction. Venue specifics stay behind thin adapters.

**Current milestone — Binance perpetual futures MVP**: Stage 1 forecasts the 10-second midprice return from L1 + trade data; Stage 2 fits a TOB-cross threshold policy. Built under explicit, documented simplifications (zero latency, zero fees, $100 max position) removed one by one toward a real-life tradeable model. Crypto is the beachhead because its data is free, continuous, and operationally simplest — the platform is not about crypto.

Equally important deliverable: validating the **agentic-iteration workflow** — Claude + ML models iterating on the system, with metric-moving improvements produced by the agentic loop.

## Documents

| Document | Purpose |
|----------|---------|
| [`mvp.md`](mvp.md) | MVP definition — goals, assumptions, two-stage pipeline, validation methodology, success gates |
| [`spec.md`](spec.md) | Living spec — conventions, feature/label catalogues, DOs/DONTs, HFT/MFT pitfalls. **Wins on conflict with `mvp.md`** |
| `.planning/` | GSD project planning — roadmap, requirements, research |

## Layout

All MVP code lives under `mvp/` (strict containment — nothing MVP-related outside it): `data/`, `features/`, `forecast/`, `monetization/`, `sim/`, `eval/`, `tests/`.

## Stack

Python: `polars` + `numpy` + `numba` (sim hot path) — **no pandas**. Model tracks: scikit-learn (regression), LightGBM (trees), PyTorch (transformer). Tracking: MLflow.

## Development

This repo uses **git-flow**. Direct commits to `main` and `develop` are blocked by a pre-commit hook.

```bash
git flow feature start <feature-name>
# Make changes...
git flow feature finish <feature-name>
```

- `main` — production releases
- `develop` — integration
- `feature/*`, `release/*`, `hotfix/*`, `bugfix/*` — work branches

## Success gate (MVP exit)

Net P&L > 0 **and** annualized Sharpe > 5 on a locked held-out walk-forward window, under stated simplifications, by at least one of the three model classes — plus at least one agentic-loop-produced model improvement that verifiably moved the validation metric.
