"""Current dependency readiness shared by commands and snapshot projections."""

from services.application.canonical_ports import CanonicalRepository
from services.application.derived_ports import (
    CandidateRepository,
    ClassificationRepository,
)
from services.domain.issues import ClassificationResult, Readiness
from services.pipeline.rules.duplicates import business_key


def dependencies_ready(
    result: ClassificationResult,
    classifications: ClassificationRepository,
    candidates: CandidateRepository,
    canonicals: CanonicalRepository,
) -> bool:
    dependencies = classifications.dependencies(result.id)
    for dependency in dependencies:
        target = dependency.target_candidate_revision_id
        if target is None:
            return False
        canonical = canonicals.for_candidate(target)
        if canonical is None:
            # Classification may point to a same-value reobservation candidate.
            candidate = candidates.get(target)
            key = business_key(candidate.payload)
            governed = canonicals.get_key(*key) if key else None
            if governed is None:
                return False
            current = canonicals.current(governed.identity_id)
            if current is None:
                return False
            from services.pipeline.rules.duplicates import same_typed_values

            if not same_typed_values(
                candidate.payload,
                candidates.get(current.candidate_revision_id).payload,
            ):
                return False
        elif canonicals.current(canonical.identity_id) != canonical:
            return False
    return bool(dependencies) or result.readiness is not Readiness.BLOCKED_BY_DEPENDENCY
