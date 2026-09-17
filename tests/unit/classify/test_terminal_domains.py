"""Terminal domain validation after governed repairs, with unchanged evidence."""

from dataclasses import replace
from pathlib import Path

import pytest

from services.domain.issues import IssueCode, Readiness, Verdict
from services.pipeline.normalise.candidates import (
    NormaliseContext,
    build_initial_candidate,
)
from services.pipeline.parse import parse_records
from tests.unit.classify.test_rules import (
    CUSTOMER,
    ORDER,
    PRODUCT,
    RUN_ID,
    SNAPSHOT,
    FixedClock,
    assess,
    graph_for,
)

VALID_PRODUCT = PRODUCT.replace("sale", "accessories")


def test_terminal_domains_actual_fixture_line_53_preserves_evidence():
    from services.pipeline.classify import classify_graph
    from services.pipeline.rules.base import RunCandidateGraph
    from services.pipeline.rules.registry import default_registry

    with Path("data/messy_sample_data.csv").open("rb") as source:
        raw = next(
            r for r in parse_records(source, RUN_ID) if r.source_line_start == 53
        )
    initial = build_initial_candidate(raw, NormaliseContext(FixedClock()))
    graph = RunCandidateGraph(
        RUN_ID,
        (raw,),
        (initial.revision,),
        SNAPSHOT,
        FixedClock().now(),
        initial.issues,
        initial.transformations,
    )
    result = classify_graph(graph, default_registry())
    assert result.results[0].verdict == Verdict.NEEDS_REVIEW
    assert result.results[0].readiness == Readiness.INELIGIBLE
    issues = {i.code: i for i in result.graph.issues}
    assert issues[IssueCode.INVALID_CATEGORY].field_path == "product.category"
    assert issues[IssueCode.UNKNOWN_TAG].field_path == "product.tags"
    assert issues[IssueCode.INVALID_CATEGORY].source_refs[0].field_index == 3
    assert issues[IssueCode.UNKNOWN_TAG].source_refs[0].field_index == 8
    assert (
        result.graph.terminal[0].payload.category == initial.revision.payload.category
    )
    assert result.graph.terminal[0].payload.tags == initial.revision.payload.tags
    assert result.graph.raw_records == (raw,)
    assert {reason.issue_id for reason in result.reviews[0].reasons} >= {
        issues[code].id for code in (IssueCode.INVALID_CATEGORY, IssueCode.UNKNOWN_TAG)
    }
    assert all(i.id.version == 5 for i in issues.values())


@pytest.mark.parametrize(
    ("csv", "code", "path"),
    [
        (
            VALID_PRODUCT.replace("SKU-2004", "SKU-00209"),
            IssueCode.INVALID_IDENTIFIER,
            "product.sku",
        ),
        (
            ORDER.replace("SKU-2004", "SKU-00209"),
            IssueCode.INVALID_IDENTIFIER,
            "order.sku",
        ),
        (
            CUSTOMER.replace("CUST-1001", "CUST-1"),
            IssueCode.INVALID_IDENTIFIER,
            "customer.customer_id",
        ),
        (
            ORDER.replace("ORD-3001", "ORD-1"),
            IssueCode.INVALID_IDENTIFIER,
            "order.order_id",
        ),
        (
            ORDER.replace("19.99,1", "19.99,0"),
            IssueCode.INVALID_INTEGER,
            "order.quantity",
        ),
        (ORDER.replace("19.99", "-1.00"), IssueCode.INVALID_AMOUNT, "order.unit_price"),
        (
            VALID_PRODUCT.replace("19.99", "-1.00"),
            IssueCode.INVALID_AMOUNT,
            "product.unit_price",
        ),
        (
            VALID_PRODUCT.replace("19.99", "0"),
            IssueCode.INVALID_AMOUNT,
            "product.unit_price",
        ),
        (ORDER.replace("19.99", "0"), IssueCode.INVALID_AMOUNT, "order.unit_price"),
        (
            CUSTOMER.replace(",100,", ",-0.01,"),
            IssueCode.INVALID_AMOUNT,
            "customer.lifetime_spend",
        ),
        (
            CUSTOMER.replace("s@example.org", "n/a"),
            IssueCode.INVALID_EMAIL,
            "customer.email",
        ),
        (
            CUSTOMER.replace("s@example.org", "one@example.org;two@example.org"),
            IssueCode.INVALID_EMAIL,
            "customer.email",
        ),
        (
            CUSTOMER.replace("Sofia Rossi 🌟", ""),
            IssueCode.MISSING_REQUIRED_VALUE,
            "customer.name",
        ),
        (
            ORDER.replace("Sofia Rossi", ""),
            IssueCode.MISSING_REQUIRED_VALUE,
            "order.customer_name_raw",
        ),
        (
            VALID_PRODUCT.replace("Widget", ""),
            IssueCode.MISSING_REQUIRED_VALUE,
            "product.name",
        ),
        (
            CUSTOMER.replace("100,,", "100,1,"),
            IssueCode.INVALID_STRUCTURE,
            "customer.quantity",
        ),
        (
            ORDER.replace("2024-01-07", ""),
            IssueCode.MISSING_REQUIRED_VALUE,
            "order.ordered_at",
        ),
        (
            CUSTOMER.replace("2024-01-01", ""),
            IssueCode.MISSING_REQUIRED_VALUE,
            "customer.signup_date",
        ),
        (
            CUSTOMER.replace("2024-01-01", "2024/01/01 10:30:00"),
            IssueCode.INVALID_DATE,
            "customer.signup_date",
        ),
        (
            VALID_PRODUCT.replace("2024-01-01", "2024/01/01 10:30:00"),
            IssueCode.INVALID_DATE,
            "product.listed_date",
        ),
        (
            VALID_PRODUCT.replace(",Electronics,", ",,"),
            IssueCode.MISSING_REQUIRED_VALUE,
            "product.category",
        ),
    ],
)
def test_terminal_domains_invalid_values_are_reviewed_without_repair(csv, code, path):
    from services.pipeline.rules.registry import default_registry

    registry = default_registry()
    graph = graph_for(csv)
    repaired = graph
    from services.pipeline.rules.base import apply_effects

    for rule in registry.rules:
        if rule.repairable:
            repaired = apply_effects(repaired, rule)
    outcome = assess(csv)
    assert outcome.results[0].verdict == Verdict.NEEDS_REVIEW
    assert outcome.results[0].readiness == Readiness.INELIGIBLE
    issue = next(
        i for i in outcome.graph.issues if i.code == code and i.field_path == path
    )
    assert issue.source_refs and issue.id.version == 5
    assert len(outcome.graph.revisions) == len(repaired.revisions)
    assert all(r.origin != "TERMINAL_FIELD_DOMAINS" for r in outcome.graph.revisions)
    if "SKU-00209" in csv:
        assert outcome.graph.terminal[0].payload.sku.value == "SKU-00209"


@pytest.mark.parametrize(
    ("csv", "code"),
    [
        (VALID_PRODUCT.replace("Electronics", "Office"), IssueCode.INVALID_CATEGORY),
        (VALID_PRODUCT.replace("Electronics", "Storage"), IssueCode.INVALID_CATEGORY),
        (VALID_PRODUCT.replace("accessories", "desk"), IssueCode.UNKNOWN_TAG),
        (VALID_PRODUCT.replace("accessories", "gaming|premium"), IssueCode.UNKNOWN_TAG),
        (CUSTOMER.replace("vip", "premium"), IssueCode.UNKNOWN_TAG),
        (CUSTOMER.replace("vip", "na"), IssueCode.UNKNOWN_TAG),
        (ORDER.replace("shipped,,", "shipped,overnight,"), IssueCode.UNKNOWN_TAG),
        (ORDER.replace("shipped,,", "shipped,priority,"), IssueCode.UNKNOWN_TAG),
        (ORDER.replace("shipped,,", "shipped,standard,"), IssueCode.UNKNOWN_TAG),
    ],
)
def test_terminal_domains_unregistered_expanded_vocabulary(csv, code):
    result = assess(csv)
    assert result.results[0].verdict == Verdict.NEEDS_REVIEW
    assert any(i.code == code for i in result.graph.issues)


@pytest.mark.parametrize(
    "csv",
    [
        CUSTOMER.replace(",100,", ",0,"),
        CUSTOMER.replace("s@example.org", "-"),
        CUSTOMER.replace("vip", "vip|newsletter|loyalty"),
        VALID_PRODUCT.replace("19.99", "0.01"),
        VALID_PRODUCT.replace("Electronics", "Home & Office").replace(
            "accessories", "home|office|ergonomic"
        ),
        VALID_PRODUCT.replace("Electronics", "Accessories").replace(
            "accessories", "limited|new"
        ),
        VALID_PRODUCT.replace("2024-01-01", ""),
        VALID_PRODUCT.replace(",5,", ",0,").replace("in_stock", "discontinued"),
        VALID_PRODUCT.replace(",5,", ",-1,").replace("in_stock", "backordered"),
        ORDER.replace("19.99", "0.01"),
        ORDER.replace("19.99,1", "19.99,-1").replace("shipped", "refunded"),
        ORDER.replace("19.99,1", "NULL,None").replace("shipped", "cancelled"),
        ORDER.replace("shipped,,", "shipped,express,"),
        ORDER.replace("2024-01-07", "2024/01/07 10:30:00"),
    ],
)
def test_terminal_domains_valid_boundaries(csv):
    result = assess(csv)
    assert result.results[0].verdict == Verdict.CLEAN
    assert not any(
        i.code != IssueCode.UNRESOLVED_REFERENCE for i in result.graph.issues
    )


def test_terminal_domains_repaired_sku_validates_after_repair():
    result = assess(VALID_PRODUCT.replace("SKU-2004", "SKU-00204"))
    assert result.results[0].verdict == Verdict.AUTO_REPAIRED
    assert result.results[0].readiness == Readiness.ELIGIBLE
    assert not any(i.code == IssueCode.INVALID_IDENTIFIER for i in result.graph.issues)


def test_terminal_domains_config_changes_hash_and_behavior():
    from services.pipeline.classify import classify_graph
    from services.pipeline.rules.domains import TerminalDomainConfig
    from services.pipeline.rules.registry import default_registry

    config = TerminalDomainConfig()
    baseline = default_registry(domain_config=config)
    changed = default_registry(
        domain_config=replace(
            config, product_categories=(*config.product_categories, "Office")
        )
    )
    assert baseline.rules_version != changed.rules_version
    result = classify_graph(
        graph_for(VALID_PRODUCT.replace("Electronics", "Office")), changed
    )
    assert result.results[0].verdict == Verdict.CLEAN
    rule = next(r for r in baseline.rules if r.id == "TERMINAL_FIELD_DOMAINS")
    assert not rule.repairable
    effects = rule.apply(graph_for(VALID_PRODUCT.replace("Electronics", "Office")))
    assert effects and all(e.revision is None for e in effects)
    for field, value in [
        ("product_tags", ("desk",)),
        ("customer_tags", ("premium",)),
        ("order_tags", ("priority",)),
        ("customer_id_pattern", r"^CUST-\d{5}$"),
        ("order_quantity_nonzero", False),
    ]:
        updated = default_registry(domain_config=replace(config, **{field: value}))
        assert updated.rules_version != baseline.rules_version
