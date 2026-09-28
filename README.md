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

## TimesFM 3.0 (exploratory — no code here uses it)

**[Google TimesFM](https://huggingface.co/google/timesfm-3.0-pytorch) forecasts a numeric series from its own history with no training.** Hand it a context window and a horizon; it hands back a point forecast and nine quantiles. Version 3.0 is the first one trained natively multivariate, so several correlated streams — BTC and ETH mid, or mid and realised volatility — go in as one array and the cross-series structure is the model's job rather than a hand-built ridge. It also takes covariates directly: past-only, and past+future for anything known ahead of time.

**Nothing in this repo imports it.** `git grep -i timesfm` turns up `.gitignore` and planning notes, and no module. No executed phase uses it, no MVP success gate depends on it, and under the CI leakage rules it cannot become a catalogued feature — an information-set proof for what a pretrained model's weights already saw is not a thing anyone knows how to write. It is here as a **baseline to beat**, and as a candidate for volatility forecasting; both are deferred experiments recorded in `.planning/STATE.md`.

**What that is worth, measured here** — context 512, horizon 32, CPU, `timesfm 3.0.2`, mean absolute error over the whole horizon:

| Series | TimesFM | Hold the last value |
|--------|---------|---------------------|
| Sine, period 64, noiseless | 0.0028 | 0.734 |
| Random walk, unit step σ, seed 0 | 2.74 | 2.95 |

On a clean periodic signal it is ~260× better than holding the last value. On a random walk it is a wash, and the forecast barely leaves the last observation at all: mean \|forecast − last value\| = 0.23 against a 1-step σ of 1.0. That second row is the honest expectation for a 10-second midprice, which is near-unpredictable by construction — and the reason TimesFM is a bar to clear rather than a feature to add.

### Where it goes, and why that is not negotiable

**A root you choose, outside this git repo, with its own virtualenv.** Never `mvp/.venv`. Pick any path — below it is called `$TIMESFM_ROOT`:

```
$TIMESFM_ROOT/
├── .venv/       # Python 3.12 + timesfm[torch] — its own, unrelated to mvp/.venv
├── hf-cache/    # the downloaded weights (1.23 GiB)
└── work/        # your own scripts and scratch output
```

Installing it into `mvp/.venv` does not merely offend a convention, it stops you committing. TimesFM pulls `torch` and `numpy` 2.5, while `mvp/.venv` is lockfile-pinned to `numpy<2.5` because `numba` requires it — so every `@njit` import breaks, and the `uv lock --check` and `check_pin_versions` pre-commit hooks fail on the drift. Nothing lands in git until the install is torn out again.

### Install

One script, from the repo root:

```bash
tools/setup-timesfm.sh /path/to/timesfm       # or: TIMESFM_ROOT=/path/to/timesfm tools/setup-timesfm.sh
```

With no argument it uses a sibling of the repo named `timesfm/`, and prints the resolved root before it writes anything. It **refuses** a root inside this repo — that refusal is the feature. It is safe to re-run: an existing venv is reused and the weights are verified rather than refetched (1.7 s on a complete install, against ~3 min for the first download).

What it does, for a reader who wants to see inside it: creates the venv with `uv venv --python 3.12`; installs with `uv pip install --python "$TIMESFM_ROOT/.venv/bin/python" "timesfm[torch]" huggingface_hub`; exports `HF_HOME` and `HUGGINGFACE_HUB_CACHE`, both at `$TIMESFM_ROOT/hf-cache`, plus `TOKENIZERS_PARALLELISM=false`; then calls `huggingface_hub.snapshot_download("google/timesfm-3.0-pytorch", cache_dir=os.environ["HUGGINGFACE_HUB_CACHE"])`. The repo is ungated — no token, no access request, though an unauthenticated download is rate-limited. What landed here: 5 files, 1.23 GiB, `timesfm 3.0.2` and `torch 2.14.0` on CPython 3.12.2, with Apple MPS reported available -- and this example was re-run with `device="mps"` offline, producing the same shapes, so MPS is verified rather than merely reported.

### Check it actually landed

Don't trust the download's own output — count the bytes yourself:

```bash
SNAP="$TIMESFM_ROOT/hf-cache/models--google--timesfm-3.0-pytorch/snapshots"
find -L "$SNAP" -type f | wc -l     # 5
du -shL "$SNAP"                     # 1.2G  (1.23 GiB, as du rounds it)
```

Both flags matter: the snapshot's entries are symlinks into the cache's blob store, so without `-L` / `-shL` you count zero files and measure a few kilobytes of links.

### A forecast, end to end

Run under `$TIMESFM_ROOT/.venv/bin/python`, with the two cache variables exported. `local_files_only=True` is deliberate: it proves the weights came from your directory instead of quietly re-downloading.

```python
import os
import numpy as np
from timesfm3 import TimesFM3Forecaster

fc = TimesFM3Forecaster.from_pretrained(
    "google/timesfm-3.0-pytorch",
    cache_dir=os.environ["HUGGINGFACE_HUB_CACHE"],
    local_files_only=True,
    device="cpu",                       # "mps" verified here; "cuda" where available
)

L, H = 512, 32
t = np.arange(L, dtype=np.float32)
mid = np.sin(2 * np.pi * t / 64.0)
vol = np.abs(np.cos(2 * np.pi * t / 64.0))

out = fc.predict(
    context=np.stack([mid, vol]),                                   # (n_variates, L)
    horizon=H,
    past_only_covariates=np.stack([np.cos(2 * np.pi * t / 64.0)]),   # (n_cov, L)
    past_future_covariates=np.stack([                                # (n_cov, L + H)
        np.sin(2 * np.pi * np.arange(L + H, dtype=np.float32) / 256.0)
    ]),
    ts_id="btcusdt",
    return_quantiles=True,
)
print(out.ts_id, out.forecast.shape, out.quantiles.shape)   # btcusdt (2, 32) (2, 32, 9)
```

`predict` returns a `ForecastOutput(ts_id, forecast, quantiles)`. A 1-D context of length `L` gives `forecast` of shape `(H,)`; the 2-D context above gives `(n_variates, H)`, with nine quantiles per step.

### Making it work with your own code

There is no integration point to wire up — no module in this repo loads these weights, so there is no config key to set and nothing to import. What a script of yours needs is exactly three things:

1. **The right interpreter**: `$TIMESFM_ROOT/.venv/bin/python`. Not `mvp/.venv`'s, for the reason above.
2. **`HUGGINGFACE_HUB_CACHE=$TIMESFM_ROOT/hf-cache`** — this is the variable the loader actually reads. `HF_HOME` alone is *not* enough: it resolves the hub cache to `$HF_HOME/hub`, which is one level below where the weights sit, and the load fails with `LocalEntryNotFoundError`. Export both anyway (the setup script does) so nothing else strays into `~/.cache`, and set `TOKENIZERS_PARALLELISM=false`. Passing `cache_dir=` explicitly, as in the example above, works instead of either.
3. **A home outside this repo**: `$TIMESFM_ROOT/work`, beside the weights. `mvp/` is reserved for MVP code by the containment rule and this is not MVP code — which is also why `tools/setup-timesfm.sh` sits at the repo root rather than under `mvp/`.

The weights cannot reach git history: `timesfm/`, `hf-cache/`, `*.safetensors`, `*.ckpt`, `*.pt` and `*.pth` are all in the root `.gitignore`. That cuts both ways — a script parked under a directory named `timesfm/` inside the repo is silently ignored too, so `git add` reports success and the file never lands.

## Success gate (MVP exit)

Net P&L > 0 **and** annualized Sharpe > 5 on a locked held-out walk-forward window, under stated simplifications, by at least one of the three model classes — plus at least one agentic-loop-produced model improvement that verifiably moved the validation metric.
