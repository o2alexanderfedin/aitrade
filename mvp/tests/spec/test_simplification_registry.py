"""Every simplification the simulator makes is in the list of simplifications,
the prose about it says what was measured, and the two spec mitigations that
anticipate it say honestly which of them exists.

WHY THIS IS A TEST AND NOT A CONVENTION. `mvp.md`'s "Assumptions (all
simplifications, listed for later removal)" list is the document a reader of a
`Net P&L > 0` claim consults to find out what the claim rests on. The
unconditional fill at the touch -- the simulator filling the whole lot at
`ask_ticks[i]`/`bid_ticks[i]` on the same decision row whose book produced the
feature, with no queue and no adverse selection -- was absent from that list for
the whole of phases 6 and 7. A bullet can be deleted in a rewrite without anybody
noticing; a failing test cannot.

AND THE OVERCLAIM IS GUARDED TOO, which is the less obvious half. The deferred
item that asked for this registration said the fill assumption is "where the
entire P&L comes from", and the first draft of the bullet repeated it -- while a
sibling commit on the same branch already carried three measurements against it
(median 2.884 BTC resting on the taken side against a 0.001 BTC order; at or
below that size on 0.19% of rows; 84% of the P&L retained at 20-40 ms of
latency). What the sign depends on is ZERO FEES: a 0.519 bp edge per round trip
that one basis point of required overshoot takes to zero on all five blocks. A
registration that overstates its own importance misdirects the next reader as
surely as a missing one, so both directions are asserted.

WHAT THIS DOES NOT DO. It does not review the prose. What it pins is the
STRUCTURE a reader needs and the NUMBERS the prose cites: the assumption is in
the assumptions list, the removal queue says which items remove it, every quoted
figure is recomputed from the evidence that produced it, the borrowed figures
appear in both documents, and each of the two `spec.md` mitigations carries a
status note saying whether it is implemented.

NO LAKE, NO MLFLOW, NO LOOK: four files on disk, two of them committed JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

#: `mvp/` -- this file is `mvp/tests/spec/test_simplification_registry.py`.
PKG_ROOT: Path = Path(__file__).resolve().parents[2]

#: The evidence the prose cites, produced by `scripts/fill_skill_gap.py` at zero
#: look cost. Outside `mvp/` because phase evidence lives with the phase.
_EVIDENCE_DIR: Path = (
    PKG_ROOT.parent
    / ".planning"
    / "phases"
    / "07-regression-track-vertical-slice"
    / "evidence"
)
EVIDENCE_PATH: Path = _EVIDENCE_DIR / "07-fill-skill-gap.json"

#: The zero-look OOF viability run, whose lag variants the same prose cites.
VIABILITY_PATH: Path = _EVIDENCE_DIR / "07-oof-viability-results.json"

#: The leakage investigation that measured what the P&L actually rests on. Its
#: numbers are quoted in `mvp.md` and the quotation is checked against it.
INVESTIGATION_PATH: Path = (
    PKG_ROOT.parent / ".planning" / "debug" / ("07-oof-pnl-leakage-investigation.md")
)

#: The figures `mvp.md` borrows from that investigation, each of which is the
#: REASON the registration says the fill assumption is secondary. A first draft
#: of the bullet claimed the opposite -- "this is the simplification that
#: carries the P&L" -- while these three numbers sat in a sibling commit on the
#: same branch saying it is not.
BORROWED_FIGURES: tuple[str, ...] = (
    "2.884 BTC",  # median resting size on the side the model takes
    "0.19%",  # rows where that side is at or below the 0.001 BTC order size
    "0.519 bp",  # the realised edge per round trip
    "84%",  # P&L retained at 20-40 ms of latency
)


def _section(text: str, heading: str) -> str:
    """The body of a markdown section, from its heading to the next heading at
    the same or a shallower level. Raises if the heading is absent, so a renamed
    section is a failure rather than an empty string that passes every `in`
    check below."""
    lines = text.splitlines()
    depth = len(heading) - len(heading.lstrip("#"))
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        raise AssertionError(
            f"no section titled {heading!r} -- it was renamed or removed, and "
            "every containment check against it would otherwise pass vacuously"
        ) from None
    for index in range(start + 1, len(lines)):
        stripped = lines[index].lstrip()
        if stripped.startswith("#"):
            if len(stripped) - len(stripped.lstrip("#")) <= depth:
                return "\n".join(lines[start:index])
    return "\n".join(lines[start:])


@pytest.fixture(scope="module")
def mvp_md() -> str:
    return (PKG_ROOT / "mvp.md").read_text()


@pytest.fixture(scope="module")
def spec_md() -> str:
    return (PKG_ROOT / "spec.md").read_text()


def test_the_unconditional_fill_is_in_the_monetization_assumptions_list(mvp_md):
    """It must be in the ASSUMPTIONS section specifically, not merely somewhere
    in the file -- a mention in the removal queue or in a summary is not the
    list a reader of a P&L claim consults."""
    section = _section(mvp_md, "### Monetization")
    assert "Unconditional fill at the touch" in section, (
        "mvp.md's Monetization assumptions no longer name the unconditional "
        "fill at the touch -- the fill mechanics the whole Stage-2 P&L is "
        "computed under. A Net P&L > 0 claim is not readable without it, "
        "whatever its rank among the simplifications turns out to be"
    )
    # The mechanism, in enough detail that the reader knows what is assumed
    # away rather than only that something is.
    for phrase in ("queue", "adverse selection", "same decision row"):
        assert phrase in section, (
            f"the entry does not mention {phrase!r} -- naming the assumption "
            "without naming what it assumes away is not a registration"
        )
    # Anti-vacuity: the section is the real one, carrying its siblings.
    assert "Zero exchange fees" in section and "Taker-only" in section


def test_the_removal_queue_says_which_items_remove_it(mvp_md):
    """A registered simplification with no removal path is a note, not a queue
    entry. Items 4 (L1-aware fills) and 5 (queue position modeling) are the
    pair, and the queue must say so where a reader of the queue will see it."""
    section = _section(mvp_md, "## Simplification removal queue (post-MVP, tentative)")
    assert "L1-aware fills" in section and "Queue position modeling" in section
    assert "unconditional fill at the touch" in section, (
        "the removal queue does not connect items 4 and 5 to the assumption "
        "they remove, so a reader of the queue cannot tell which entry retires "
        "the fill mechanics the P&L was computed under"
    )


def test_the_numbers_in_the_prose_are_the_numbers_in_the_committed_evidence(mvp_md):
    """THE ASSERTION WITH TEETH: the two ranges `mvp.md` quotes are recomputed
    from `07-fill-skill-gap.json` and must round to the quoted figures.

    Prose that cites a measurement is a claim about a file. Without this, the
    citation could drift from the evidence in either direction -- the evidence
    regenerated, or the prose edited -- and every other assertion here would
    still pass.
    """
    assert EVIDENCE_PATH.is_file(), (
        f"{EVIDENCE_PATH} is missing -- mvp.md cites it by name, so the "
        "citation is unverifiable and the numbers in the prose are unsourced. "
        "Regenerate with `./.venv/bin/python3 -m scripts.fill_skill_gap` "
        "(zero looks, cached frames only)"
    )
    blocks = json.loads(EVIDENCE_PATH.read_text())["blocks"]
    assert len(blocks) == 5, f"{len(blocks)} blocks; the prose quotes all five"

    mid_ic = [
        b["metrics_by_target"]["mid_next_row_change_half_ticks"]["rank_ic_all"]
        for b in blocks
    ]
    fill_ic = [
        b["metrics_by_target"]["fill_next_row_long_ticks"]["rank_ic_all"]
        for b in blocks
    ]
    eaten = [b["fraction_of_those_rows_the_spread_ate"] for b in blocks]
    shrink = [1.0 - fill / mid for mid, fill in zip(mid_ic, fill_ic, strict=True)]

    section = _section(mvp_md, "### Monetization")
    quoted = (
        f"+{min(mid_ic):.3f} to +{max(mid_ic):.3f}",
        f"+{min(fill_ic):.3f} to +{max(fill_ic):.3f}",
        f"{round(min(shrink) * 100)}% to {round(max(shrink) * 100)}%",
        f"{round(min(eaten) * 100)}% to {round(max(eaten) * 100)}%",
    )
    print("recomputed from the evidence:", quoted)
    for figure in quoted:
        assert figure in section, (
            f"mvp.md does not quote {figure!r}, which is what "
            f"{EVIDENCE_PATH.name} actually measures -- the prose and the "
            "evidence have drifted apart"
        )
    # Anti-vacuity: the gap is a real reduction, not a rounding artifact.
    assert all(f < m for f, m in zip(fill_ic, mid_ic, strict=True)), (
        "the fillable-proxy IC is not below the mid IC on every block, so "
        "there is no skill gap to report and the prose overstates it"
    )


def test_the_lag_retention_in_the_prose_is_the_lag_retention_in_the_evidence(mvp_md):
    """The second citation, from the OTHER evidence file, recomputed the same way.

    The first draft of this bullet said `frozen_lag1` cost P&L "on four of the
    five blocks" and that `frozen_lag100` cost "about half". Both were wrong --
    it costs P&L on all five, and lag100 retains 28.8% to 51.5%, not ~50% -- and
    nothing but this recomputation would have caught either. That is the whole
    argument for pinning prose to evidence rather than to memory.
    """
    assert VIABILITY_PATH.is_file(), f"{VIABILITY_PATH} is missing"
    blocks = json.loads(VIABILITY_PATH.read_text())["blocks"]
    assert len(blocks) == 5

    def retention(variant: str) -> list[float]:
        return [
            b["variants"][variant]["by_x_bps"]["0"]["closed_pnl_ticks"]
            / b["variants"]["frozen"]["by_x_bps"]["0"]["closed_pnl_ticks"]
            for b in blocks
        ]

    lag1, lag100 = retention("frozen_lag1"), retention("frozen_lag100")
    section = _section(mvp_md, "### Monetization")
    quoted = (
        f"{min(lag1) * 100:.1f}% to {max(lag1) * 100:.1f}%",
        f"{min(lag100) * 100:.1f}% to {max(lag100) * 100:.1f}%",
    )
    print("lag retention recomputed:", quoted)
    for figure in quoted:
        assert figure in section, (
            f"mvp.md does not quote {figure!r}, which is what "
            f"{VIABILITY_PATH.name}'s lag variants actually measure"
        )
    assert all(value < 1.0 for value in lag1), (
        "a one-row delay does not cost P&L on every block, so the prose's "
        "'all five' is wrong -- which is exactly the claim this test exists to "
        "keep honest"
    )


def test_the_registration_does_not_claim_the_fill_assumption_carries_the_pnl(mvp_md):
    """THE CORRECTION THIS TEST EXISTS FOR, and the one a future rewrite is most
    likely to undo.

    The deferred item this registration answers said the unconditional fill is
    "where the entire P&L comes from", and the first draft of the bullet repeated
    it in bold. A sibling investigation on the same branch had already measured
    the opposite and its numbers are borrowed into the bullet: the taken side
    carries a median 2.884 BTC against a 0.001 BTC order and is at or below the
    order size on 0.19% of rows, so the typical fill is NOT against a vanishing
    queue; and the realised edge is 0.519 bp per round trip, which one basis
    point of required overshoot takes to zero on all five blocks -- so the sign
    depends on ZERO FEES, queue item 1.

    Every borrowed figure must appear in BOTH documents, so neither can drift
    from the other, and the overclaim must not be back.
    """
    assert INVESTIGATION_PATH.is_file(), (
        f"{INVESTIGATION_PATH} is missing -- mvp.md's registration borrows four "
        "measurements from it and the borrowing is unverifiable without it"
    )
    investigation = INVESTIGATION_PATH.read_text()
    section = _section(mvp_md, "### Monetization")
    for figure in BORROWED_FIGURES:
        assert figure in section, (
            f"mvp.md no longer quotes {figure!r}, one of the measurements that "
            "says the fill assumption is secondary to zero fees"
        )
        assert figure in investigation, (
            f"{INVESTIGATION_PATH.name} no longer contains {figure!r}, so "
            "mvp.md is citing a number its source does not carry"
        )
    lowered = section.lower()
    for overclaim in (
        "simplification that carries the p&l",
        "where the entire p&l comes from",
        "about to disappear",
    ):
        assert overclaim not in lowered, (
            f"the registration says {overclaim!r} again. The measured resting "
            "size (median 2.884 BTC against a 0.001 BTC order, at or below it "
            "on 0.19% of rows) contradicts it, and the 0.519 bp edge against a "
            "1 bp threshold says zero FEES is what the sign depends on"
        )
    assert "zero fees" in lowered, (
        "the registration must name the simplification the sign actually "
        "depends on, or a reader takes the fill assumption for the main risk"
    )


@pytest.mark.parametrize(
    ("heading", "must_say"),
    [
        ("### Label engineering", "PARTIALLY IMPLEMENTED"),
        ("### Forecast-vs-execution gap", "NOT IMPLEMENTED"),
    ],
)
def test_each_anticipating_spec_mitigation_says_whether_it_exists(
    spec_md, heading, must_say
):
    """`spec.md` asked for both halves of this years before the measurement: an
    IC on a fillable proxy under "Label engineering", and an immediate-after-fill
    reversion histogram under "Forecast-vs-execution gap". One now exists and one
    does not, and each section must say which -- an unqualified **Enforcement**
    line reads as a description of the system.
    """
    section = _section(spec_md, heading)
    assert "**Status (" in section, (
        f"{heading} carries an Enforcement line with no status note, so a "
        "reader cannot tell whether the enforcement is implemented"
    )
    assert must_say in section, (
        f"{heading}'s status note does not say {must_say!r}; the two "
        "mitigations are in different states and one note for both erases the "
        "distinction"
    )
