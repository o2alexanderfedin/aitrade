---
slug: readme-timesfm-setup
type: quick
created: 2026-09-27
autonomous: true
---

# Document how to get the Google TimesFM model and where to put it

Add one section to the repo-root `README.md`: what TimesFM is for here, how to
fetch it with its weights, exactly where it goes, and why it must not go
anywhere near `mvp/.venv`.

## Source of truth — verify, do not invent

A working install already exists on this machine, deliberately OUTSIDE the repo:

- `/Volumes/ProjectsSSD/aihedgefund/timesfm/setup.sh` — the script that worked
- `/Volumes/ProjectsSSD/aihedgefund/timesfm/setup.log` — what it printed

READ BOTH FIRST. Every command in the README must be one of those, or one you
ran yourself. Do not document a flag you have not seen work.

## Tasks

1. Read `setup.sh`, `setup.log`, the repo-root `.gitignore`, and the existing
   `README.md` (headings today: `## Documents`, `## Layout`, `## Stack`,
   `## Development`, `## Success gate (MVP exit)`). Match that voice and depth.
   Confirm with `git grep -i timesfm` that NO committed code imports it.
2. Write the section. Commit `README.md` alone.
3. Write `SUMMARY.md` here and add a row to STATE.md's
   "Quick Tasks Completed" table. Commit that separately.

## What the section must contain

**Lead with the capability, not the install.** TimesFM 3.0 forecasts a numeric
series from its own history with no training, and 3.0 is the first version
trained natively multivariate, so several correlated streams go in together.

**Say plainly what it is NOT**, high up. It is exploratory. Nothing in this repo
imports it, no executed phase uses it, and the MVP pipeline does not depend on
it. A reader must not conclude otherwise. The reason to bother, measured in a
smoke test on this machine: on a clean sine wave it beat a last-value baseline
by roughly 330x; on a random walk it collapses to holding the last value (mean
absolute error 0.370 against a 1-step sigma of 1.0) — the honest expectation for
a 10-second midprice, and the reason it is a baseline to beat rather than a
feature.

**Where it goes, and why that is not negotiable.** A reader-chosen root OUTSIDE
this git repo, with its own virtualenv — never `mvp/.venv`. State the
consequence, not just the rule: `mvp/.venv` is lockfile-pinned, and the
`uv lock --check` and `check_pin_versions` hooks fail on any drift, so a `torch`
installed there blocks every commit until it is removed. Show the layout:
venv, `hf-cache/` for weights, a scratch dir.

**The steps**, copy-pasteable, with a `$TIMESFM_ROOT` the reader sets:
`uv venv --python 3.12`; `uv pip install --python "$TIMESFM_ROOT/.venv/bin/python"
"timesfm[torch]" huggingface_hub`; export `HF_HOME` and `HUGGINGFACE_HUB_CACHE`
both at `$TIMESFM_ROOT/hf-cache` and `TOKENIZERS_PARALLELISM=false`; then
`huggingface_hub.snapshot_download("google/timesfm-3.0-pytorch",
cache_dir=os.environ["HUGGINGFACE_HUB_CACHE"])`. The repo is ungated — no token,
no access request. Landed here as 5 files, 1.23 GiB; installed timesfm 3.0.2 and
torch 2.14.0 under Python 3.12, with Apple MPS available.

**How to check it actually landed** — a command that prints the file count and
total size, so the reader is not trusting the download's own output.

**A minimal working example** using the real API: `TimesFM3Forecaster`,
`.from_pretrained(...)`, then `.predict(context, horizon,
past_only_covariates=..., past_future_covariates=..., return_quantiles=True)`,
which returns `ForecastOutput(ts_id, forecast, quantiles)`.

**How to make your own code find it** — this is the "works with current code"
part and it must be honest: no module in this repo reads the weights, so any
script you write sets `HF_HOME`/`HUGGINGFACE_HUB_CACHE` itself or passes
`cache_dir=` explicitly, and runs under the TimesFM venv's interpreter, never
`mvp/.venv`'s. Say where such a script belongs (the scratch dir beside the
weights, outside the repo) and why: the containment rule keeps MVP code under
`mvp/`, and this is not MVP code.

**Weights stay untracked.** Read `.gitignore` and confirm in the README that
`timesfm/`, `hf-cache/`, `*.safetensors`, `*.ckpt`, `*.pt` and `*.pth` are
already covered, so the 1.23 GiB cannot reach git history.

## Prohibitions

- **Keep the legal framing out entirely — the user has ruled that topic
  irrelevant to this project.** Not a sentence, not a parenthetical, not a
  link. Standing instruction, non-negotiable.
- Do not invent an integration that does not exist.
- Never pass `--no-verify`. All hooks must pass.
- Do not push and do not merge — the caller handles that.
- Work only from `/Volumes/ProjectsSSD/aihedgefund/repo`; `cd` there every call.

## Commit trailer — exactly these two lines, nothing after

```
Co-Authored-By: AI Hive(R) <sales@hupyy.com>
Claude-Session: https://claude.ai/code/session_01RCuyRej4LNRLXQnszUC9Ho
```

## Done when

`README.md` has the section, every command in it is one you saw work, `git
status` is clean, all hooks passed, and the SUMMARY states what you did NOT do.
