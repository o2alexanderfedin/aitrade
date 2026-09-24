"""06-04-PLAN.md Task 2: the permanent, hermetic cross-check that D-06-19's
regression proof holds.

Reads ONLY two committed JSON files with plain `json.load` -- no lake
mount, no manifest resolution, no `features.tier.load_features` call (so
this file needs no `tools/check_harness_accessor_only.py` sanction). Runs
in ordinary CI, where `/Volumes/ProjectsSSD` is never mounted.

`06-04-v1-v2-label-diff.json` (this plan's own measured evidence, Task 2's
one-off script) and the errata manifest (`22190ad9...json`, computed
independently in Phase 5 via `harness.errata.compute_errata_cells` -- a
different mechanism, a different phase, re-deriving the same set from the
lake directly) must name the EXACT SAME 249 cells. Set equality, not
subset/superset: either evidence file drifting from the other is exactly
the "a green migration that proves nothing" failure T-06-12 names.
"""

from __future__ import annotations

import json
from pathlib import Path

# mvp/tests/features/test_schema_v2_regression.py -> parents[0]=features,
# [1]=tests, [2]=mvp, [3]=repo root. Both committed JSON files live under
# the repo root (one under .planning/, one under mvp/data/lake_registry/),
# so the anchor must be the repo root, not PKG_ROOT.
REPO_ROOT = Path(__file__).resolve().parents[3]

DIFF_EVIDENCE_PATH = (
    REPO_ROOT
    / ".planning"
    / "phases"
    / "06-event-driven-simulator"
    / "evidence"
    / "06-04-v1-v2-label-diff.json"
)

ERRATA_MANIFEST_PATH = (
    REPO_ROOT
    / "mvp"
    / "data"
    / "lake_registry"
    / "errata"
    / "22190ad925929001855c4cec56c7ddbd00043d4717ec02ac96fc9b71e3e31b58.json"
)

_CELL_KEYS = ("date", "etime", "decision_seq", "label_column")


def _cell_set(cells: list[dict]) -> set[tuple]:
    return {tuple(c[k] for k in _CELL_KEYS) for c in cells}


def test_v1_v2_label_diff_equals_the_committed_errata_cell_set() -> None:
    diff = json.loads(DIFF_EVIDENCE_PATH.read_text())
    errata = json.loads(ERRATA_MANIFEST_PATH.read_text())

    diff_cells = _cell_set(diff["cells"])
    errata_cells = _cell_set(errata["cells"])

    assert diff_cells == errata_cells, (
        f"06-04's measured v1-vs-v2 label diff does not match the "
        f"independently-computed errata manifest's cell set -- "
        f"only in diff: {sorted(diff_cells - errata_cells)[:5]}; "
        f"only in errata: {sorted(errata_cells - diff_cells)[:5]}"
    )
    assert len(diff_cells) == 249, (
        f"expected exactly 249 cells (D-06-19), got {len(diff_cells)}"
    )
    assert len(diff["cells"]) == 249, (
        "the raw cell list must also be exactly 249 entries -- a duplicate "
        "cell would pass the set-equality check above while silently "
        "under-counting"
    )


def test_v1_v2_label_diff_cells_are_confined_to_the_named_errata_dates_and_columns() -> (
    None
):
    diff = json.loads(DIFF_EVIDENCE_PATH.read_text())
    allowed_dates = {"2026-09-12", "2026-09-13"}
    allowed_columns = {"ret_1s_mid", "ret_10s_mid"}
    for cell in diff["cells"]:
        assert cell["date"] in allowed_dates, (
            f"cell {cell} is dated outside the two known errata days -- "
            "D-06-19 confines the 249-cell difference to 2026-09-12/13 only"
        )
        assert cell["label_column"] in allowed_columns, (
            f"cell {cell} names a label column outside "
            f"{sorted(allowed_columns)} -- not part of the known errata shape"
        )
