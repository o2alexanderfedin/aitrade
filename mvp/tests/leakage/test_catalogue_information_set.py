"""The two silent failures nobody notices: a declaration the code exceeds,
and a declaration nothing implements.

The per-feature and per-label properties in this directory prove things
about the names they are written against. That leaves two holes neither can
see: a catalogue entry with no implementation (proved about nothing), and
an implementation column with no entry (implemented, undeclared, unproved).
Both fail here.
"""

from __future__ import annotations

import numpy as np
import pytest

from data.time_ns import LABEL_HORIZON_NS, NS_PER_SECOND
from features.labels import compute_labels
from features.reference import FEATURE_OUTPUT_NAMES
from spec.catalogue import load_features, load_labels
from spec.information_set import InformationSetError, parse_information_set
from tests.leakage.test_feature_information_set import FEATURE_NAMES
from tests.leakage.test_label_information_set import LABEL_NAMES

#: Kernel output columns that are NOT catalogued features. `warmup` is
#: D-04-09's flag travelling with the row, not a value a model reads; it has
#: no catalogue entry by design. Anything else appearing in
#: `FEATURE_OUTPUT_NAMES` is an undeclared feature and fails below.
NON_FEATURE_OUTPUTS: frozenset[str] = frozenset({"warmup"})


def _label_outputs() -> set[str]:
    """The label column names the implementation actually emits, from a
    two-row synthetic problem -- the cheapest honest answer to "what does
    `compute_labels` produce", as opposed to re-reading the catalogue it is
    being compared against."""
    quote_etime = np.arange(0, 4000, dtype=np.int64) * NS_PER_SECOND
    quote_mid = np.full(quote_etime.size, 100.0, dtype=np.float64)
    decision_etime = np.array([0, NS_PER_SECOND], dtype=np.int64)
    decision_mid = np.array([100.0, 100.0], dtype=np.float64)
    labels, _ = compute_labels(decision_etime, decision_mid, quote_etime, quote_mid)
    return set(labels)


def test_every_catalogued_name_has_an_implementation_and_vice_versa():
    """Both directions, for both catalogues."""
    declared_features = set(load_features())
    implemented_features = set(FEATURE_OUTPUT_NAMES) - NON_FEATURE_OUTPUTS
    assert declared_features == implemented_features, (
        "features declared but not implemented: "
        f"{sorted(declared_features - implemented_features)}; implemented but "
        f"not declared: {sorted(implemented_features - declared_features)}"
    )

    declared_labels = set(load_labels())
    implemented_labels = _label_outputs()
    assert declared_labels == implemented_labels, (
        "labels declared but not implemented: "
        f"{sorted(declared_labels - implemented_labels)}; implemented but not "
        f"declared: {sorted(implemented_labels - declared_labels)}"
    )
    assert declared_labels == set(LABEL_HORIZON_NS)


def test_every_catalogued_declaration_parses():
    """The explicit-value tests in `tests/spec/test_information_set.py` pin
    what each FORM means; this one pins COVERAGE -- a fifth entry whose
    declaration the grammar cannot read fails here, instead of being
    interpreted generously by whichever property test reaches it first."""
    for name, entry in sorted(load_features().items()):
        parsed = parse_information_set(entry.information_set)
        assert parsed.lookahead_ns == 0, (
            f"{name} is a FEATURE and declares a lookahead of "
            f"{parsed.lookahead_ns} ns -- a feature that reads past t is a "
            "leak, whatever the catalogue says"
        )
    for name, entry in sorted(load_labels().items()):
        parsed = parse_information_set(entry.information_set)
        assert parsed.lookahead_ns > 0, (
            f"{name} is a LABEL and declares no lookahead at all"
        )


def test_an_uncatalogued_declaration_form_is_refused_not_guessed():
    """Anti-vacuity for the coverage test above: it passes because the
    strings parse, not because the parser accepts anything."""
    with pytest.raises(InformationSetError):
        parse_information_set("whatever the code happens to read")


def test_every_feature_and_label_is_covered_by_a_leakage_property():
    """T-04-27. The properties in this directory are written against named
    features and labels. A FIFTH catalogue entry would be proved nothing
    about -- silently, since every existing property would still pass.

    This is the test that keeps the suite honest as Phase 8 adds features:
    a new entry fails here until someone writes its property and adds its
    name to the tuple, which is also the moment they have to decide what
    its information set means.
    """
    assert set(load_features()) == set(FEATURE_NAMES), (
        "features with no leakage property: "
        f"{sorted(set(load_features()) - set(FEATURE_NAMES))}; leakage "
        f"properties for features that are not catalogued: "
        f"{sorted(set(FEATURE_NAMES) - set(load_features()))}"
    )
    assert set(load_labels()) == set(LABEL_NAMES), (
        "labels with no leakage property: "
        f"{sorted(set(load_labels()) - set(LABEL_NAMES))}; leakage properties "
        f"for labels that are not catalogued: "
        f"{sorted(set(LABEL_NAMES) - set(load_labels()))}"
    )
