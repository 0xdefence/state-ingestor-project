"""Explicit semantic ordering and canonical content hashing, never discovery order."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from functools import partial
from hashlib import sha256
from types import MappingProxyType
from typing import cast

from services.pipeline.rules.base import (
    RuleDefinition,
    RunCandidateGraph,
    apply_effects,
)
from services.pipeline.rules.domains import TerminalDomainConfig, terminal_domains
from services.pipeline.rules.duplicates import comparisons
from services.pipeline.rules.invariants import refund_invariant, stock_invariant
from services.pipeline.rules.relationships import REFERRAL_PATTERN, relationships
from services.pipeline.rules.repairs import (
    SKU_REPAIRS,
    bind_fx,
    repair_line_totals,
    repair_skus,
)

# Versioned semantic configuration; changing normalization requires updating its
# contract here. Build/source-control revisions deliberately are not hash inputs.
NORMALIZER_CONFIG: Mapping[str, object] = MappingProxyType(
    {
        "assembly": "registered-shapes-v1",
        "money": "approved-decimal-formats-v1",
        "dates": "approved-formats-v1;slash=MDY",
        "match_key": "NFKC;remove-So;collapse-whitespace;casefold",
        "status": "entity-status-registry-v1",
        "tags": "ordered-lowercase-pipe-v1",
        "fx": "ECB;precision=28;ROUND_HALF_EVEN;GBP=0.01",
    }
)


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, object], value)
        return {key: _json_value(item) for key, item in mapping.items()}
    if isinstance(value, (tuple, list)):
        return [
            _json_value(item) for item in cast(tuple[object, ...] | list[object], value)
        ]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ValueError(
        "Rule config must use JSON strings, integers, booleans or containers"
    )


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType(
            {str(k): _freeze(v) for k, v in cast(dict[str, object], value).items()}
        )
    if isinstance(value, list):
        return tuple(_freeze(v) for v in cast(list[object], value))
    return value


@dataclass(frozen=True, slots=True)
class RuleRegistry:
    rules: tuple[RuleDefinition, ...]
    normalizer_config: Mapping[str, object] = field(
        default_factory=lambda: NORMALIZER_CONFIG
    )
    rules_version: str = field(init=False)

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.rules, key=lambda r: (r.order, r.id)))
        if len({r.id for r in ordered}) != len(ordered):
            raise ValueError("Rule IDs must be unique")
        if any(not r.id or r.version < 1 for r in ordered):
            raise ValueError("Rules require an ID and positive semantic version")
        config = json.loads(
            json.dumps(
                _json_value(self.normalizer_config), sort_keys=True, allow_nan=False
            )
        )
        definitions = [
            {
                "id": r.id,
                "version": r.version,
                "repairable": r.repairable,
                "order": r.order,
                "parameters": sorted(r.parameters),
            }
            for r in ordered
        ]
        content = json.dumps(
            {"rules": definitions, "normalizer": config},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        object.__setattr__(self, "rules", ordered)
        object.__setattr__(self, "normalizer_config", _freeze(config))
        object.__setattr__(self, "rules_version", sha256(content.encode()).hexdigest())

    def apply(self, graph: RunCandidateGraph) -> RunCandidateGraph:
        graph.check_barrier()
        graph = replace(graph, rules_version=self.rules_version)
        for rule in self.rules:
            graph = apply_effects(graph, rule)
        return graph


def _always(graph: RunCandidateGraph) -> bool:
    return True


def default_registry(
    *, domain_config: TerminalDomainConfig = TerminalDomainConfig()
) -> RuleRegistry:
    return RuleRegistry(
        (
            RuleDefinition(
                "SKU_ZERO_PADDING", 1, True, _always, repair_skus, 10, SKU_REPAIRS
            ),
            RuleDefinition(
                "LINE_TOTAL_REPAIRED",
                1,
                True,
                _always,
                repair_line_totals,
                20,
                (("currencies", "same-or-USD/GBP"), ("quantity", ">1")),
            ),
            RuleDefinition(
                "FX_BINDING",
                1,
                True,
                _always,
                bind_fx,
                30,
                (("order", "ordered_at"), ("other", "run_snapshot")),
            ),
            RuleDefinition(
                "TERMINAL_FIELD_DOMAINS",
                1,
                False,
                _always,
                partial(terminal_domains, config=domain_config),
                35,
                domain_config.parameters,
            ),
            RuleDefinition(
                "OBSERVATION_COMPARISONS", 1, False, _always, comparisons, 38
            ),
            RuleDefinition(
                "RELATIONSHIPS",
                1,
                False,
                _always,
                relationships,
                40,
                (("referral", REFERRAL_PATTERN),),
            ),
            RuleDefinition("REFUND_INVARIANT", 1, False, _always, refund_invariant, 50),
            RuleDefinition(
                "STOCK_STATUS_INVARIANTS", 1, False, _always, stock_invariant, 60
            ),
        )
    )
