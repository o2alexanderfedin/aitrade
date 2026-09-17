# Lockbox Policy

**Canonical, git-committed copy.** This is the copy agents and humans
actually read -- a copy placed inside `lake/lockbox/` itself would become
unreadable the moment that directory is `chmod 0000`'d, which defeats the
point of a policy document. If you found a stub `POLICY.md` inside
`lake/lockbox/` on the SSD, it exists only to point back here.

## What this protects against, stated honestly

The held-out lockbox exists because one of this project's operators is an
autonomous agent, and agents are specifically good at enthusiastically
loading all available data (PITFALLS.md #14). A glob over Parquet
partitions, a "let me just check the date range" exploration, or a plotting
script that loads everything can silently invalidate the held-out window
with nobody knowing -- none of those announce themselves as a violation.

Phase 3 delivers **two mechanical barriers plus an audit trail against a
same-uid, NON-adversarial actor**:

1. **No code path.** `data/store.py`, the default curated-tier loader, has
   no code path that constructs a path under `lockbox/` -- not a flag, not
   a default argument. Enforced by `tools/check_lockbox_containment.py`
   (an AST scan, not a grep) against every `*.py` file in `mvp/` except
   `data/lockbox.py` itself.
2. **`chmod 0000` on the physical directory.** MEASURED on this machine's
   APFS volume: a same-uid `open()`, `iterdir()`, or `glob()` against a
   `chmod 0000` directory raises `PermissionError`. This is a real,
   working barrier against accidental access.

## What this does NOT protect against -- read this part

**The same uid that owns `lake/lockbox/` can always `chmod 0755` it back
and read everything.** `chmod 0000` stops accidents, not deliberate action.
A determined same-uid agent -- or a tired human -- can reverse it in one
command. This is not a security boundary against an adversarial or even
just insistently curious same-uid process; it is a speed bump that makes
the *accidental* case (the one PITFALLS #14 is actually worried about)
mechanically impossible instead of merely discouraged.

**Formal agent-proofing is Phase 10's job**, not this one: the agentic
loop's sandbox is meant to simply not mount the lockbox path at all. Until
that sandbox exists, do not describe this mechanism, in any docstring, test
name, or report, as making the lockbox unreadable to an agent. It does not.
It makes an *accidental* read mechanically impossible and every
*deliberate* read visible in an MLflow record and a git diff.

## The three components

### 1. Physical separation

`lake/lockbox/` is a sibling root to `lake/curated/` and `lake/raw/` on the
SSD (`/Volumes/ProjectsSSD/aihedgefund/lake/lockbox/`), same partition
layout. Not populated with real data in Phase 3 -- the held-out date range
is chosen by the fold harness (Phase 5) and locked at v0 (Phase 8,
EVAL-06). Phase 3's red-proof (RP-3) quarantines a synthetic fixture
segment only.

### 2. `chmod 0000`

The physical `lake/lockbox/` directory is `chmod 0000`'d once it is
populated. **Operational procedure for a legitimate, human-invoked gate
evaluation** (the only sanctioned reason to ever read it):

```
chmod 0755 /Volumes/ProjectsSSD/aihedgefund/lake/lockbox/   # lift the barrier
# ... run the human-invoked gate-evaluation script, which calls
#     data.lockbox.open_lockbox() ...
chmod 0000 /Volumes/ProjectsSSD/aihedgefund/lake/lockbox/   # re-apply it
```

`data.lockbox.open_lockbox()` handles the TOKEN layer only -- it never
lifts or re-applies the `chmod` barrier itself. Lifting/re-applying it is a
deliberate, out-of-band, human-run step, by design: automating it would
turn the physical barrier into exactly the kind of "flag or default
argument" the missing-code-path control is built to avoid.

### 3. One-look unlock token

A token is a git-committed JSON file at
`mvp/data/lake_registry/lockbox_tokens/<token_id>.json` (see
`data/lake_paths.py`'s docstring for why this lives under the git-tracked
registry root rather than CONTEXT.md's literal `lake/lockbox/tokens/` path
-- the latter is SSD-only and invisible to `git log`/GitHub Actions, which
would make "every look appears in a diff" false):

```json
{
  "token_id": "...",
  "segment_manifest_id": "...",
  "dataset": "...",
  "purpose": "...",
  "gate": "...",
  "requested_by": "...",
  "created_at": 1234567890,
  "consumed_at": null,
  "mlflow_run_id": null
}
```

`data.lockbox.open_lockbox(token_id, purpose, requested_by, tracking_root,
...)`:

- Verifies `requested_by` against the token's own stored value.
- Holds an exclusive `<token_id>.lock` (created with `O_EXCL` beside the
  token JSON) for the whole check-stamp-read sequence, so two concurrent
  opens cannot both pass the checks. A stale lock after a crash fails
  closed: confirm the token's MLflow record, then remove it by hand.
- Refuses a `tracking_root` that does not already contain `mlflow.db`
  (pointing the call at any other directory used to create a fresh, empty
  store there and answer "never consumed"), and one whose `mlflow.db` is
  not an initialised MLflow SQLite store -- zero bytes, not SQLite, or
  missing MLflow's tables (03-REVIEW-ITER2.md WR-12). The token JSON is
  read only while the lock is held.
- Checks consumption **MLflow-first**: `MlflowClient().search_runs(...)`
  across every experiment at `tracking_root`, **including soft-deleted runs
  and experiments** (`ViewType.ALL` -- MLflow's default is active-only, so
  deleting the access run in the UI used to re-arm the token), filtered to
  `tags.lockbox_token_id == token_id`. This is the DURABLE check, queried
  BEFORE the JSON's own `consumed_at` field, because a same-uid
  `git checkout -- <token>.json` can silently revert the JSON stamp but
  cannot revert an MLflow run record the same way.
- **Any exception the MLflow query itself raises propagates unmodified.**
  It is never caught and treated as "not consumed" -- an unreachable or
  reset tracking root must refuse the open, not silently re-arm a
  previously consumed token.
- Only then checks the JSON's own `consumed_at` field (cheaper,
  belt-and-suspenders, still useful if MLflow itself is unreachable).
- Stamps `consumed_at` **atomically, before returning any rows or even
  attempting the read** -- a crash between the stamp and the read leaves
  the token looking burned, never silently reusable. This asymmetry (fail
  toward "looks consumed" rather than "looks available") is deliberate.
- Logs the look through `tracking.mlflow_utils.start_tracked_run` -- the
  project's ONLY MLflow entry point, never a second `mlflow.start_run`
  call -- tagged with all 8 mandatory keys plus `lockbox_access="true"`,
  `lockbox_token_id`, `lockbox_purpose`.

**Residual, honestly-stated gap:** if the real `mlflow.db` is deleted and
replaced by an empty (or different) store, or its rows are purged with
`mlflow gc`, AND the token JSON's `consumed_at` stamp is simultaneously
reverted, the two signals together are indistinguishable from "never
consumed." Either signal alone (an intact tracking store OR an unreverted
JSON stamp) still catches it. The same holds when the caller simply passes a
`tracking_root` holding some OTHER initialised MLflow store: the canonical
tracking root is not pinned, so only the JSON stamp refuses that second look.

## Agent containment (PITFALLS #14)

No agent-run script may reference `lake/lockbox/` or `data/lockbox.py`'s
private (underscore-prefixed) internals outside a deliberate, human-invoked
gate evaluation. This is stated in the repo-root `CLAUDE.md` (the copy
every agent session reads) and in `mvp/spec.md`'s DONTs list, and enforced
mechanically by `tools/check_lockbox_containment.py` (AST-resolved, not a
grep -- see that module's own docstring for its exact scope, exemptions,
and accepted static-analysis gaps).
