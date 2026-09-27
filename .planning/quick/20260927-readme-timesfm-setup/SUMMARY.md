---
slug: readme-timesfm-setup
type: quick
created: 2026-09-27
completed: 2026-09-27
---

# Summary — document how to get TimesFM and where to put it

A public-facing README section now tells a reader with no access to this machine
how to install Google TimesFM 3.0 into a root they choose, how to confirm the
1.23 GiB actually landed, and how to point their own script at it — plus one
runnable script, `tools/setup-timesfm.sh`, that does the install and refuses to
do it inside the repo.

## Commits

| Commit | What |
|--------|------|
| `5e35c90` | `tools/setup-timesfm.sh` — idempotent installer with the in-repo refusal |
| `889d578` | `README.md` — the `## TimesFM 3.0 (exploratory — no code here uses it)` section |
| this one | `SUMMARY.md`, `PLAN.md`, STATE.md row |

## What the script does that the working original did not

The source of truth was `setup.sh` beside the existing install, outside the repo.
It hardcodes its own location and assumes this Mac. The committed script:

- takes the install root from `$1`, `$TIMESFM_ROOT`, or a sibling of the repo
  named `timesfm/`, and prints the resolved root before writing anything;
- **refuses** any root inside the git repo, canonicalising first so `./x` and
  `mvp/../mvp/x` are both caught, and removing the directory it just created;
- resolves the repo root from the script's own location, not the caller's cwd,
  so it is runnable from anywhere; warns and continues if it is not in a git
  repo at all, since the hazard is specifically this repo's pinned `mvp/.venv`;
- reuses an existing venv and lets `snapshot_download` verify the cache instead
  of refetching;
- fails early, with an install hint, when `uv` is missing;
- prints versions, device availability, file count and total size — and *only*
  the count and size, not the per-file listing the original emitted.

It lives at the repo root, not under `mvp/`: `mvp/` is for MVP code and this is
not MVP code. It cannot live under `timesfm/` — `git check-ignore -v
timesfm/setup.sh` resolves to `.gitignore:1:timesfm/`, so a script there would
be silently ignored, `git add` would report nothing, and the commit would look
like it worked. `git ls-files -s tools/` confirms the committed file is tracked
at mode `100755`.

## Verified by running

| Claim | How |
|-------|-----|
| Refusal fires | Three roots inside the repo — relative `./scratch-timesfm`, `mvp/../mvp/hf-junk`, and `$TIMESFM_ROOT` set to the repo root — each exited `3`, printed the resolved path, and left nothing behind (`git status` clean, no stray directories) |
| Idempotent | Re-run against the existing install, from a cwd outside the repo, with no argument (so the default-root branch ran): **1.67 s wall**, against ~3 min for the original download. Argument form re-run too |
| Non-destructive | `uv pip install --dry-run` first: "Checked 2 packages … Would make no changes" — the existing working install was never mutated |
| The README example | Extracted from `README.md` itself with `awk` and run under the TimesFM interpreter offline; printed `btcusdt (2, 32) (2, 32, 9)`, exactly its commented output |
| Weight discovery | `HUGGINGFACE_HUB_CACHE` is the variable that matters. With `HF_HOME` alone the hub cache resolves to `$HF_HOME/hub` and the load fails with `LocalEntryNotFoundError` — measured both ways, with `HF_HUB_OFFLINE=1` |
| The landing check | `find -L … \| wc -l` → 5 and `du -shL …` → 1.2G |
| No integration exists | `git grep -i timesfm` re-run: `.gitignore` and planning notes only, no module |
| API shapes | `TimesFM3Forecaster.from_pretrained` / `.predict(..., past_only_covariates=, past_future_covariates=, return_quantiles=True)` → `ForecastOutput(ts_id, forecast, quantiles)`; `(2, 32)` and `(2, 32, 9)` for a two-variate context |
| Hooks | All 18 passed on both commits, never bypassed |

## Verified by reading only

The installed package source, for the `predict` signature and the covariate
shape convention `(n_covariates, length)` — then confirmed by running a call
with both covariate kinds.

## Where the plan was wrong, and what I changed

1. **The smoke-test numbers.** The plan quoted ~330x on a sine wave and mean
   absolute error 0.370 on a random walk, from a run whose script is not on
   disk (`work/` was empty), so it is not reproducible. I re-measured with a
   stated configuration — context 512, horizon 32, CPU, sine period 64, random
   walk seed 0 — and got **260x** and **0.23**. The README carries my numbers
   together with the configuration, so a reader can reproduce them.
2. **The random walk result is not "worse than the baseline".** On this seed
   TimesFM slightly beat holding the last value (MAE 2.74 vs 2.95). What
   collapses is the *shape* of the forecast: it barely leaves the last
   observation. The README says that, rather than implying it scores worse.
3. **"Export `HF_HOME` and `HUGGINGFACE_HUB_CACHE` both"** is true but hides
   which one is load-bearing. Only `HUGGINGFACE_HUB_CACHE` makes the loader
   find weights in that directory; `HF_HOME` alone looks one level too deep.
   The README states this, with the failure it produces.
4. **My own first draft of the landing check was wrong** and running it caught
   that: `find "$ROOT/hf-cache" -type f | wc -l` counts **18**, not 5, because
   the cache also holds blobs, locks and a chunk cache. The committed command
   points at the snapshot directory and follows symlinks.
5. **The script was not in the plan** — it arrived as a mid-execution
   requirement and became task 1.

## What I did NOT do

- **I did not push, merge, or open a PR**, and did not leave the branch
  `feature/phase-07-regression-track-vertical-slice`.
- **I did not re-download the weights** and did not modify the existing install
  beside them — the dry run proved the re-run changes nothing. The script did
  create an empty `work/` directory there, which already existed.
- **I did not touch `mvp/.venv`**, and ran no `uv add`, `uv sync` or `uv lock`.
  Both commits went through the full hook set, including `uv lock --check`; no
  `--no-verify`, ever.
- **I did not write, wire, or reference any integration code.** No module,
  entry point, config key or import path in this repo reads these weights, and
  the README says so instead of inventing one.
- **I did not verify MPS or CUDA execution.** `torch.backends.mps.is_available()`
  reports `True` here; every forecast in this task ran on CPU, and the README's
  example says `device="cpu"` for that reason.
- **I did not benchmark TimesFM on real market data.** The two synthetic series
  are a smoke test of the install, not evidence about the 10-second midprice.
  The deferred experiments E1 and E2 in STATE.md remain deferred.
- **I did not add a shellcheck hook** or any CI coverage for the new script; it
  is checked by `bash -n` and by having been run, nothing more.
