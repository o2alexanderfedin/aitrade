# aitrade

ML trading research for Binance perpetual futures: a two-stage pipeline that forecasts the 10-second midprice return (Stage 1) and fits a TOB-cross threshold monetization policy on those forecasts in simulation (Stage 2). Built under explicit, documented simplifications (zero latency, zero fees, $100 max position) that are removed one by one toward a real-life tradeable model.

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
