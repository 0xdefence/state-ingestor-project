"""Terminal field domains: validate repaired values without changing evidence.

Vocabularies are the approved original fixture segment (physical lines 2–25),
not the anomalous expanded segment. Every setting is part of the rule hash.
"""

import json
import re
from dataclasses import dataclass, fields
from datetime import datetime
from typing import cast

from services.domain.candidates import (
    CustomerCandidate,
    EvidenceField,
    Money,
    OrderCandidate,
    ProductCandidate,
    RejectedCandidateShell,
)
from services.domain.fields import FieldState, SourceRef
from services.domain.issues import IssueCode
from services.pipeline.rules.base import RuleEffect, RunCandidateGraph
from services.pipeline.rules.invariants import validation_issue


@dataclass(frozen=True, slots=True)
class TerminalDomainConfig:
    customer_id_pattern: str = r"^CUST-\d{4}$"
    product_sku_pattern: str = r"^SKU-\d{4}$"
    order_id_pattern: str = r"^ORD-\d{4}$"
    email_pattern: str = r"[^\s@,;]+@[^\s@,;]+"
    product_categories: tuple[str, ...] = (
        "Accessories",
        "Electronics",
        "Home & Office",
    )
    customer_tags: tuple[str, ...] = ("loyalty", "newsletter", "vip")
    product_tags: tuple[str, ...] = (
        "accessories",
        "electronics",
        "ergonomic",
        "home",
        "limited",
        "new",
        "office",
    )
    order_tags: tuple[str, ...] = ("express",)
    customer_statuses: tuple[str, ...] = ("active", "inactive")
    product_statuses: tuple[str, ...] = (
        "backordered",
        "discontinued",
        "in_stock",
        "pending_review",
    )
    order_statuses: tuple[str, ...] = ("cancelled", "pending", "refunded", "shipped")
    required_fields: tuple[str, ...] = (
        "customer.customer_id",
        "customer.name",
        "customer.lifetime_spend",
        "customer.signup_date",
        "customer.status",
        "product.sku",
        "product.name",
        "product.category",
        "product.unit_price",
        "product.stock_qty",
        "product.status",
        "order.order_id",
        "order.customer_name_raw",
        "order.sku",
        "order.unit_price",
        "order.quantity",
        "order.ordered_at",
        "order.status",
    )
    date_only_fields: tuple[str, ...] = ("customer.signup_date", "product.listed_date")
    nonnegative_money_fields: tuple[str, ...] = ("customer.lifetime_spend",)
    positive_money_fields: tuple[str, ...] = ("product.unit_price", "order.unit_price")
    order_quantity_nonzero: bool = True
    customer_quantity_empty: bool = True
    cancelled_nullable_fields: tuple[str, ...] = ("order.unit_price", "order.quantity")
    pending_review_unresolved_fields: tuple[str, ...] = (
        "product.unit_price",
        "product.stock_qty",
        "product.listed_date",
    )

    @property
    def parameters(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (item.name, json.dumps(getattr(self, item.name), separators=(",", ":")))
            for item in fields(self)
        )


def terminal_domains(
    graph: RunCandidateGraph, config: TerminalDomainConfig
) -> tuple[RuleEffect, ...]:
    effects: list[RuleEffect] = []
    raws = {raw.id: raw for raw in graph.raw_records}
    for revision in graph.terminal:
        payload = revision.payload
        if isinstance(payload, RejectedCandidateShell):
            continue
        entity = payload.entity_type.value
        # Payload dataclasses enumerate the typed fields; annotations are not fields.
        values = {
            f"{entity}.{item.name}": cast(EvidenceField, getattr(payload, item.name))
            for item in fields(payload)
            if item.name
            not in ("entity_type", "unit_price_annotation", "lifetime_spend_annotation")
        }
        checks: list[tuple[str, IssueCode, str]] = []
        for path in config.required_fields:
            value = values.get(path)
            if value is None or value.state is FieldState.KNOWN or value.issue_refs:
                continue
            if (
                isinstance(payload, OrderCandidate)
                and payload.status.value == "cancelled"
                and path in config.cancelled_nullable_fields
                and value.state is FieldState.ABSENT
            ) or (
                isinstance(payload, ProductCandidate)
                and payload.status.value == "pending_review"
                and path in config.pending_review_unresolved_fields
            ):
                continue
            checks.append(
                (
                    path,
                    IssueCode.MISSING_REQUIRED_VALUE,
                    f"Required field is {value.state.value}.",
                )
            )
        patterns = {
            "customer.customer_id": config.customer_id_pattern,
            "product.sku": config.product_sku_pattern,
            "order.sku": config.product_sku_pattern,
            "order.order_id": config.order_id_pattern,
        }
        for path, pattern in patterns.items():
            value = values.get(path)
            if (
                value is not None
                and isinstance(value.value, str)
                and not re.fullmatch(pattern, value.value)
            ):
                checks.append(
                    (
                        path,
                        IssueCode.INVALID_IDENTIFIER,
                        f"Identifier {value.value!r} does not match {pattern!r}.",
                    )
                )
        for path, value in values.items():
            if isinstance(value.value, Money):
                amount = value.value.amount
                if path in config.positive_money_fields and amount <= 0:
                    checks.append(
                        (
                            path,
                            IssueCode.INVALID_AMOUNT,
                            f"Amount {amount} must be positive.",
                        )
                    )
                if path in config.nonnegative_money_fields and amount < 0:
                    checks.append(
                        (
                            path,
                            IssueCode.INVALID_AMOUNT,
                            f"Amount {amount} must be non-negative.",
                        )
                    )
            if path in config.date_only_fields and isinstance(value.value, datetime):
                checks.append(
                    (
                        path,
                        IssueCode.INVALID_DATE,
                        "Expected a date without a time component.",
                    )
                )
        if isinstance(payload, CustomerCandidate):
            statuses, vocabulary = config.customer_statuses, config.customer_tags
            email = payload.email.value
            if email is not None and not re.fullmatch(config.email_pattern, email):
                checks.append(
                    (
                        "customer.email",
                        IssueCode.INVALID_EMAIL,
                        f"Expected one email address or absence; found {email!r}.",
                    )
                )
            raw = raws[revision.raw_record_id]
            if config.customer_quantity_empty and raw.fields[5].strip():
                effects.append(
                    validation_issue(
                        graph,
                        revision,
                        IssueCode.INVALID_STRUCTURE,
                        "customer.quantity",
                        f"Customer quantity must be empty; found {raw.fields[5]!r}.",
                        (SourceRef(raw.id, 5),),
                    )
                )
        elif isinstance(payload, ProductCandidate):
            statuses, vocabulary = config.product_statuses, config.product_tags
            category = payload.category.value
            if category is not None and category not in config.product_categories:
                checks.append(
                    (
                        "product.category",
                        IssueCode.INVALID_CATEGORY,
                        f"Category {category!r} is outside the registered vocabulary.",
                    )
                )
        else:
            statuses, vocabulary = config.order_statuses, config.order_tags
            if config.order_quantity_nonzero and payload.quantity.value == 0:
                checks.append(
                    (
                        "order.quantity",
                        IssueCode.INVALID_INTEGER,
                        "Order quantity must be non-zero.",
                    )
                )
        if payload.status.value is not None and payload.status.value not in statuses:
            checks.append(
                (
                    f"{entity}.status",
                    IssueCode.INVALID_STATUS,
                    f"Status {payload.status.value!r} is outside "
                    "the registered domain.",
                )
            )
        if payload.tags.value is not None:
            unknown = tuple(tag for tag in payload.tags.value if tag not in vocabulary)
            if unknown:
                checks.append(
                    (
                        f"{entity}.tags",
                        IssueCode.UNKNOWN_TAG,
                        f"Tags {unknown!r} are outside the registered vocabulary.",
                    )
                )
        for path, code, summary in checks:
            effects.append(
                validation_issue(
                    graph, revision, code, path, summary, values[path].source_refs
                )
            )
    return tuple(effects)
