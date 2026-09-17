"""LOD-01..06: terminal verdict/readiness selection from real classifications."""

import pytest

from services.domain.issues import Readiness, Verdict
from tests.unit.classify.test_rules import CUSTOMER, ORDER, PRODUCT, assess


@pytest.mark.parametrize(
    ("csv", "expected", "verdict"),
    [
        (PRODUCT, 1, Verdict.CLEAN),
        (PRODUCT.replace("SKU-2004", "SKU-00204"), 1, Verdict.AUTO_REPAIRED),
        (PRODUCT.replace(",5,", ",many,"), 0, Verdict.NEEDS_REVIEW),
        ("UNKNOWN,x\n", 0, Verdict.REJECTED),
        (PRODUCT + PRODUCT, 1, Verdict.DUPLICATE),
        (ORDER, 0, Verdict.CLEAN),
    ],
)
def test_terminal_selection(csv, expected, verdict):
    from services.application.load import eligible_candidates

    summary = assess(csv)
    chosen = eligible_candidates(summary.graph.revisions, summary.results)
    assert len(chosen) == expected
    assert summary.results[-1].verdict == verdict
    assert all(c in summary.graph.terminal for c in chosen)
    assert {c.id for c in chosen} == {
        r.candidate_revision_id
        for r in summary.results
        if r.verdict in (Verdict.CLEAN, Verdict.AUTO_REPAIRED)
        and r.readiness is Readiness.ELIGIBLE
    }
    if verdict in (Verdict.NEEDS_REVIEW, Verdict.REJECTED, Verdict.DUPLICATE):
        assert summary.reviews[-1].reasons


def test_nonterminal_classification_is_rejected():
    from dataclasses import replace

    from services.application.load import eligible_candidates

    summary = assess(CUSTOMER + PRODUCT + ORDER)
    revised = next(r for r in summary.graph.terminal if r.revision_number > 1)
    results = tuple(
        replace(r, candidate_revision_id=revised.parent_revision_id)
        if r.candidate_revision_id == revised.id
        else r
        for r in summary.results
    )
    with pytest.raises(ValueError, match="terminal"):
        eligible_candidates(summary.graph.revisions, results)
