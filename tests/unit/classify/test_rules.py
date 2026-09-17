"""CLS-01..06 and CLS-13..16: actual rule effects over normalized evidence."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from services.domain.candidates import (
    CustomerCandidate,
    Money,
    OrderCandidate,
    ProductCandidate,
)
from services.domain.fx import FxRate, FxSnapshot
from services.domain.issues import (
    DependencyKind,
    DependencyState,
    IssueCode,
    Readiness,
    TransformationCode,
    Verdict,
)
from services.domain.runs import RunState
from services.pipeline.normalise.candidates import (
    NormaliseContext,
    build_initial_candidate,
)
from services.pipeline.parse import parse_records

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)
RUN_ID = UUID("ca42ca01-19e8-4c41-bb6d-66ed13a18864")
SNAPSHOT = FxSnapshot(
    UUID("04f03d6c-4bea-5271-a447-e5ec2e522684"),
    "a" * 64,
    NOW,
    "ECB",
    "https://example.org/ecb",
    (
        FxRate("GBP", date(2024, 1, 5), Decimal("0.8"), "https://example.org/ecb"),
        FxRate("USD", date(2024, 1, 5), Decimal("1.0"), "https://example.org/ecb"),
        FxRate("GBP", date(2026, 9, 16), Decimal("0.9"), "https://example.org/ecb"),
        FxRate("USD", date(2026, 9, 16), Decimal("1.0"), "https://example.org/ecb"),
    ),
)
CUSTOMER = (
    "CUSTOMER,CUST-1001,Sofia Rossi 🌟,s@example.org,100,,2024-01-01,active,vip,\n"
)
PRODUCT = "PRODUCT,SKU-2004,Widget,Electronics,19.99,5,2024-01-01,in_stock,sale,\n"
ORDER = "ORDER,ORD-3001,Sofia Rossi,SKU-2004,19.99,1,2024-01-07,shipped,,\n"


class FixedClock:
    def now(self):
        return NOW


def graph_for(csv, snapshot=SNAPSHOT):
    from services.pipeline.rules.base import RunCandidateGraph

    raws = tuple(parse_records(BytesIO(csv.encode()), RUN_ID))
    results = tuple(
        build_initial_candidate(raw, NormaliseContext(FixedClock())) for raw in raws
    )
    return RunCandidateGraph(
        RUN_ID,
        raws,
        tuple(r.revision for r in results if r.revision),
        snapshot,
        NOW,
        issues=tuple(i for r in results for i in r.issues),
        transformations=tuple(t for r in results for t in r.transformations),
    )


def assess(csv, snapshot=SNAPSHOT):
    from services.pipeline.classify import classify_graph
    from services.pipeline.rules.registry import default_registry

    return classify_graph(graph_for(csv, snapshot), default_registry())


def test_classification_barrier_waits_for_all_initial_candidates():
    from services.pipeline.classify import classify_run
    from services.pipeline.rules.registry import default_registry

    graph = graph_for(CUSTOMER + PRODUCT)
    uow = Mock()
    uow.__enter__ = Mock(return_value=uow)
    uow.__exit__ = Mock(return_value=False)
    uow.runs.get.return_value = SimpleNamespace(
        state=RunState.NORMALISED, fx_snapshot_id=SNAPSHOT.id
    )
    uow.raw_records.for_run.return_value = graph.raw_records
    uow.candidates.for_run.return_value = graph.revisions[:1]
    with pytest.raises(ValueError, match="initial candidate barrier"):
        classify_run(RUN_ID, default_registry(), uow, FixedClock())
    for writer in (
        uow.candidates.add,
        uow.classifications.add,
        uow.reviews.add,
        uow.runs.set_state,
        uow.commit,
        uow.events.append,
    ):
        writer.assert_not_called()


def test_rules_version_is_content_deterministic():
    from services.pipeline.rules.registry import RuleRegistry, default_registry

    registry = default_registry()
    reordered = RuleRegistry(
        tuple(reversed(registry.rules)), registry.normalizer_config
    )
    assert registry.rules_version == reordered.rules_version
    assert len(registry.rules_version) == 64
    changed = RuleRegistry(
        (replace(registry.rules[0], version=2), *registry.rules[1:]),
        registry.normalizer_config,
    )
    assert changed.rules_version != registry.rules_version
    changed = RuleRegistry(
        registry.rules, {**registry.normalizer_config, "match_key": "different"}
    )
    assert changed.rules_version != registry.rules_version
    config = {"nested": {"enabled": True}}
    frozen = RuleRegistry(registry.rules, config)
    original = frozen.rules_version
    config["nested"]["enabled"] = False
    assert frozen.rules_version == original


def test_sku_zero_padding_repair_appends_revision():
    graph = graph_for(PRODUCT.replace("SKU-2004", "SKU-00204"))
    from services.pipeline.classify import classify_graph
    from services.pipeline.rules.registry import default_registry

    outcome = classify_graph(graph, default_registry())
    initial = graph.revisions[0]
    repaired = next(
        r for r in outcome.graph.revisions if r.origin == "SKU_ZERO_PADDING"
    )
    assert initial.payload.sku.value == "SKU-00204"
    assert repaired.payload.sku.value == "SKU-2004"
    assert repaired.parent_revision_id == initial.id
    assert repaired.revision_number == 2 and repaired.id.version == 5
    assert repaired.payload.sku.source_refs == initial.payload.sku.source_refs
    assert repaired.payload.sku.transformation_refs
    assert outcome.results[0].verdict == Verdict.AUTO_REPAIRED
    assert any(i.code == IssueCode.SKU_ZERO_PADDING for i in outcome.graph.issues)
    assert (
        assess(PRODUCT.replace("SKU-2004", "SKU-00209"))
        .graph.terminal[0]
        .payload.sku.value
        == "SKU-00209"
    )


def test_order_sku_repair_resolves_repaired_product():
    outcome = assess((CUSTOMER + PRODUCT + ORDER).replace("SKU-2004", "SKU-00204"))
    order = next(
        r for r in outcome.graph.terminal if isinstance(r.payload, OrderCandidate)
    )
    product = next(
        r for r in outcome.graph.terminal if isinstance(r.payload, ProductCandidate)
    )
    assert order.payload.sku.value == product.payload.sku.value == "SKU-2004"
    result = next(r for r in outcome.results if r.candidate_revision_id == order.id)
    dependency = next(
        d
        for d in outcome.dependencies
        if d.classification_id == result.id and d.kind == DependencyKind.PRODUCT
    )
    assert dependency.state == DependencyState.RESOLVED
    assert dependency.resolved_entity_id is None
    target = next(
        d
        for d in outcome.graph.dependencies
        if d.source_revision_id == order.id and d.kind == DependencyKind.PRODUCT
    )
    assert target.target_revision_id == product.id
    assert result.readiness == Readiness.ELIGIBLE


def test_line_total_repair_precedes_fx_conversion():
    outcome = assess(CUSTOMER + PRODUCT + ORDER.replace("19.99,1", "$39.98,2"))
    revisions = [
        r for r in outcome.graph.revisions if isinstance(r.payload, OrderCandidate)
    ]
    assert [r.payload.unit_price.value for r in revisions[:2]] == [
        Money(Decimal("39.98"), "USD"),
        Money(Decimal("19.99"), "USD"),
    ]
    assert revisions[1].origin == "LINE_TOTAL_REPAIRED"
    assert revisions[-1].payload.unit_price_gbp.value == Decimal("15.99")
    events = [
        t
        for t in outcome.graph.transformations
        if t.candidate_revision_id in {r.id for r in revisions}
    ]
    repair = next(
        t for t in events if t.operation == TransformationCode.LINE_TOTAL_REPAIRED
    )
    fx = next(
        t
        for t in events
        if t.operation == TransformationCode.FX_CONVERTED_AT_ORDER_DATE
    )
    assert repair.sequence < fx.sequence
    assert fx.after.value.source_amount == Decimal("19.99")
    assert fx.after.value.requested_date == date(2024, 1, 7)
    assert fx.after.value.publication_date == date(2024, 1, 5)
    assert fx.after.value.snapshot_id == SNAPSHOT.id
    assert repair.id in revisions[-1].payload.unit_price.transformation_refs
    assert fx.id in revisions[-1].payload.unit_price_gbp.transformation_refs


def test_validation_only_rule_never_appends_revision():
    from services.domain.candidates import candidate_revision_id
    from services.pipeline.classify import classify_graph
    from services.pipeline.rules.base import RuleDefinition, RuleEffect
    from services.pipeline.rules.registry import RuleRegistry

    graph = graph_for(PRODUCT)
    initial = graph.revisions[0]
    child = replace(
        initial,
        id=candidate_revision_id(initial.raw_record_id, 2),
        revision_number=2,
        parent_revision_id=initial.id,
        origin="BAD",
    )
    rule = RuleDefinition(
        "BAD",
        1,
        False,
        lambda _: True,
        lambda _: (RuleEffect(initial.id, revision=child),),
        order=1,
    )
    with pytest.raises(ValueError, match="validation-only"):
        classify_graph(graph, RuleRegistry((rule,), {}))
    outcome = assess(PRODUCT.replace(",5,", ",0,"))
    assert not any(
        r.origin == "STOCK_STATUS_INVARIANTS" for r in outcome.graph.revisions
    )
    assert outcome.results[0].verdict == Verdict.NEEDS_REVIEW
    assert outcome.reviews[0].reasons[0].issue_id in {
        i.id for i in outcome.graph.issues
    }


def test_dependency_readiness_is_separate_from_verdict():
    outcome = assess(ORDER)
    assert outcome.results[0].verdict == Verdict.CLEAN
    assert outcome.results[0].readiness == Readiness.BLOCKED_BY_DEPENDENCY
    assert {d.referenced_business_value for d in outcome.dependencies} == {
        "SKU-2004",
        "Sofia Rossi",
    }
    assert all(d.resolved_entity_id is None for d in outcome.dependencies)
    assert len(outcome.reviews) == 1
    assert all(r.dependency_refs for r in outcome.reviews[0].reasons)


def test_name_match_uses_match_key_and_retains_raw_name():
    outcome = assess(CUSTOMER + PRODUCT + ORDER)
    customer = next(
        r for r in outcome.graph.terminal if isinstance(r.payload, CustomerCandidate)
    )
    order = next(
        r for r in outcome.graph.terminal if isinstance(r.payload, OrderCandidate)
    )
    assert customer.payload.name.value == "Sofia Rossi 🌟"
    assert order.payload.customer_name_raw.value == "Sofia Rossi"
    match = next(
        d
        for d in outcome.graph.dependencies
        if d.source_revision_id == order.id and d.kind == DependencyKind.CUSTOMER
    )
    assert match.target_revision_id == customer.id
    assert outcome.graph.raw_records[0].fields[2] == "Sofia Rossi 🌟"


@pytest.mark.parametrize(
    ("quantity", "status", "valid"),
    [
        (-1, "refunded", True),
        (-1, "shipped", False),
        (1, "refunded", False),
        (1, "shipped", True),
    ],
)
def test_refund_invariant(quantity, status, valid):
    outcome = assess(
        ORDER.replace("19.99,1", f"19.99,{quantity}").replace("shipped", status)
    )
    issues = [i for i in outcome.graph.issues if i.code == IssueCode.REFUND_CONFLICT]
    assert bool(issues) is not valid
    assert not any(r.origin == "REFUND_INVARIANT" for r in outcome.graph.revisions)


@pytest.mark.parametrize(
    ("quantity", "status", "valid"),
    [
        (-1, "backordered", True),
        (0, "discontinued", True),
        (1, "in_stock", True),
        (0, "backordered", False),
        (-1, "discontinued", False),
        (0, "in_stock", False),
    ],
)
def test_product_stock_status_invariants(quantity, status, valid):
    outcome = assess(
        PRODUCT.replace(",5,", f",{quantity},").replace("in_stock", status)
    )
    issues = [
        i for i in outcome.graph.issues if i.code == IssueCode.STOCK_STATUS_CONFLICT
    ]
    assert bool(issues) is not valid


def test_fx_run_snapshot_binding_and_missing_rate_review():
    outcome = assess(CUSTOMER.replace(",100,", ",$100,"))
    terminal = outcome.graph.terminal[0]
    assert terminal.payload.lifetime_spend_gbp.value == Decimal("90.00")
    event = next(
        t
        for t in outcome.graph.transformations
        if t.operation == TransformationCode.FX_CONVERTED_AT_RUN_DATE
    )
    assert event.after.value.requested_date == NOW.date()
    assert outcome.results[0].fx_snapshot_id == SNAPSHOT.id
    missing = assess(CUSTOMER.replace(",100,", ",$100,"), replace(SNAPSHOT, rates=()))
    assert missing.results[0].verdict == Verdict.NEEDS_REVIEW
    assert missing.results[0].readiness == Readiness.INELIGIBLE
    assert any(i.code == IssueCode.FX_RATE_UNAVAILABLE for i in missing.graph.issues)
    assert missing.graph.terminal[0].payload.lifetime_spend.value == Money(
        Decimal("100"), "USD"
    )


def test_referral_relationship_and_unready_target_block_readiness():
    referred = (
        CUSTOMER.replace("CUST-1001", "CUST-1002")
        .replace("Sofia Rossi 🌟", "Ada")
        .replace("vip,\n", "vip,Referred by CUST-1001\n")
    )
    outcome = assess(
        CUSTOMER.replace(",active,", ",unknown,") + referred + PRODUCT + ORDER
    )
    customer = next(
        r
        for r in outcome.graph.terminal
        if isinstance(r.payload, CustomerCandidate)
        and r.payload.customer_id.value == "CUST-1002"
    )
    dependency = next(
        d for d in outcome.graph.dependencies if d.source_revision_id == customer.id
    )
    assert dependency.kind == DependencyKind.REFERRAL
    result = next(r for r in outcome.results if r.candidate_revision_id == customer.id)
    assert result.verdict == Verdict.CLEAN
    assert result.readiness == Readiness.BLOCKED_BY_DEPENDENCY
    order = next(
        r for r in outcome.graph.terminal if isinstance(r.payload, OrderCandidate)
    )
    assert (
        next(
            r for r in outcome.results if r.candidate_revision_id == order.id
        ).readiness
        == Readiness.BLOCKED_BY_DEPENDENCY
    )


def test_all_derived_identities_are_deterministic():
    csv = (
        CUSTOMER
        + PRODUCT.replace("SKU-2004", "SKU-00204")
        + ORDER.replace("19.99,1", "$39.98,2").replace("SKU-2004", "SKU-00204")
    )
    first, second = assess(csv), assess(csv)
    assert first == second
    for value in (
        *first.graph.revisions,
        *first.graph.issues,
        *first.graph.transformations,
        *first.results,
        *first.dependencies,
        *first.reviews,
    ):
        assert value.id.version == 5


def test_referral_from_fixture_phrase_is_resolved():
    referred = CUSTOMER.replace("CUST-1001", "CUST-1002").replace(
        "vip,\n", "vip,Signed up via referral from CUST-1001\n"
    )
    outcome = assess(CUSTOMER + referred)
    dependencies = [
        d for d in outcome.graph.dependencies if d.kind == DependencyKind.REFERRAL
    ]
    assert len(dependencies) == 1
    assert dependencies[0].referenced_business_value == "CUST-1001"
    assert dependencies[0].state == DependencyState.RESOLVED


def test_rules_version_accepts_its_frozen_nested_config():
    from services.pipeline.rules.registry import RuleRegistry, default_registry

    original = RuleRegistry(
        default_registry().rules, {"nested": {"forms": ["MDY", "ISO"]}}
    )
    assert (
        RuleRegistry(original.rules, original.normalizer_config).rules_version
        == original.rules_version
    )


def test_line_total_provenance_names_quantity_and_product_basis():
    outcome = assess(PRODUCT + ORDER.replace("19.99,1", "$39.98,2"))
    revision = next(
        r for r in outcome.graph.revisions if r.origin == "LINE_TOTAL_REPAIRED"
    )
    refs = revision.payload.unit_price.source_refs
    assert {(r.raw_record_id, r.field_index) for r in refs} >= {
        (outcome.graph.raw_records[0].id, 4),
        (outcome.graph.raw_records[1].id, 4),
        (outcome.graph.raw_records[1].id, 5),
    }
