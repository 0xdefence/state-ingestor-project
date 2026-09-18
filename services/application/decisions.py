"""Transactional review commands; no transport-specific error semantics."""

from dataclasses import dataclass
from uuid import UUID

from services.application.errors import ApplicationValidationError
from services.application.ports import Clock, UnitOfWork
from services.domain.candidates import CandidateRevision
from services.domain.canonical import (
    CanonicalBusinessKey,
    CanonicalIdentity,
    CanonicalPromotionEvent,
    CanonicalRevision,
    PromotionAction,
)
from services.domain.decisions import (
    DecisionOutcome,
    DecisionValidationError,
    EffectiveReviewState,
    ReviewDecision,
    legal_outcomes,
    validate_actor,
)
from services.domain.ids import new_id
from services.domain.issues import ClassificationResult, Readiness, Verdict
from services.pipeline.rules.duplicates import business_key


class StaleDecisionError(ValueError):
    pass


class IllegalDecisionError(ValueError):
    pass


class IdempotencyConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DecideReview:
    review_item_id: UUID
    candidate_revision_id: UUID
    expected_sequence: int
    outcome: DecisionOutcome
    operator_name: str
    reason: str | None
    idempotency_key: str
    supersedes_decision_id: UUID | None = None

    def __post_init__(self) -> None:
        if type(self.expected_sequence) is not int or self.expected_sequence < 0:
            raise ApplicationValidationError("expected sequence must be nonnegative")
        try:
            validate_actor(
                self.outcome, self.operator_name, self.reason, self.idempotency_key
            )
        except DecisionValidationError as error:
            raise ApplicationValidationError(str(error)) from error


@dataclass(frozen=True, slots=True)
class DecisionResult:
    decision: ReviewDecision
    canonical_revision: CanonicalRevision | None
    effective_state: EffectiveReviewState
    replayed: bool


def _matches(command: DecideReview, decision: ReviewDecision) -> bool:
    return command == DecideReview(
        decision.review_item_id,
        decision.candidate_revision_id,
        decision.sequence - 1,
        decision.outcome,
        decision.operator_name,
        decision.reason,
        decision.idempotency_key,
        decision.supersedes_decision_id,
    )


def _dependencies_ready(result: ClassificationResult, uow: UnitOfWork) -> bool:
    dependencies = uow.classifications.dependencies(result.id)
    for dependency in dependencies:
        target = dependency.target_candidate_revision_id
        if target is None:
            return False
        canonical = uow.canonicals.for_candidate(target)
        if canonical is None:
            # Classification may point to a same-value reobservation candidate.
            candidate = uow.candidates.get(target)
            key = business_key(candidate.payload)
            governed = uow.canonicals.get_key(*key) if key else None
            if governed is None:
                return False
            current = uow.canonicals.current(governed.identity_id)
            if current is None:
                return False
            from services.pipeline.rules.duplicates import same_typed_values

            if not same_typed_values(
                candidate.payload,
                uow.candidates.get(current.candidate_revision_id).payload,
            ):
                return False
        elif uow.canonicals.current(canonical.identity_id) != canonical:
            return False
    return bool(dependencies) or result.readiness is not Readiness.BLOCKED_BY_DEPENDENCY


def _promote(
    candidate: CandidateRevision,
    decision_id: UUID | None,
    uow: UnitOfWork,
    clock: Clock,
) -> CanonicalRevision:
    key = business_key(candidate.payload)
    if key is None:
        raise IllegalDecisionError("Approval requires a governed business key")
    governed = uow.canonicals.lock_key(*key)
    if governed is None:
        identity = CanonicalIdentity(new_id(), key[0], clock.now())
        uow.canonicals.add_identity(identity)
        identity_id = identity.id
    else:
        identity_id = governed.identity_id
    current = uow.canonicals.current(identity_id)
    revision = uow.canonicals.for_candidate(candidate.id)
    if revision is None:
        revision = CanonicalRevision(
            new_id(),
            identity_id,
            candidate.id,
            uow.canonicals.next_revision_number(identity_id),
            clock.now(),
        )
        uow.canonicals.add_revision(revision)
    if governed is None:
        uow.canonicals.add_key(CanonicalBusinessKey(identity_id, *key, revision.id))
    uow.canonicals.activate(
        CanonicalPromotionEvent(
            new_id(),
            identity_id,
            revision.id,
            PromotionAction.ACTIVATE,
            decision_id,
            current.id if current else None,
            clock.now(),
        )
    )
    return revision


def _unblock_dependants(uow: UnitOfWork, clock: Clock) -> None:
    remaining = list(uow.classifications.blocked())
    while remaining:
        promoted = False
        for result in tuple(remaining):
            candidate = uow.candidates.get(result.candidate_revision_id)
            if (
                result.verdict not in (Verdict.CLEAN, Verdict.AUTO_REPAIRED)
                or uow.candidates.terminal(candidate.raw_record_id).id != candidate.id
                or uow.canonicals.for_candidate(candidate.id) is not None
            ):
                remaining.remove(result)
                continue
            if _dependencies_ready(result, uow):
                key = business_key(candidate.payload)
                # A different canonical observation may have appeared since
                # classification. Automatic unblocking cannot approve that conflict.
                if key is None:
                    remaining.remove(result)
                    continue
                governed = uow.canonicals.lock_key(*key)
                if (
                    governed is not None
                    and uow.canonicals.current(governed.identity_id) is not None
                ):
                    remaining.remove(result)
                    continue
                _promote(candidate, None, uow, clock)
                remaining.remove(result)
                promoted = True
        if not promoted:
            break


def decide_review(
    command: DecideReview, uow: UnitOfWork, clock: Clock
) -> DecisionResult:
    with uow:
        uow.decisions.lock_idempotency(command.idempotency_key)
        stored = uow.decisions.by_idempotency_key(command.idempotency_key)
        if stored is not None:
            if not _matches(command, stored):
                raise IdempotencyConflictError(
                    "Idempotency key belongs to a different decision payload"
                )
            revision = (
                uow.canonicals.for_candidate(stored.candidate_revision_id)
                if stored.outcome is DecisionOutcome.APPROVE
                else None
            )
            return DecisionResult(stored, revision, stored.effective_state, True)
        # This must precede every canonical key/identity lock, including keys
        # discovered later by the dependency cascade and approval reversals.
        uow.canonicals.lock_promotions()
        item = uow.reviews.get_locked(command.review_item_id)
        classification = uow.classifications.get(item.classification_id)
        candidate = uow.candidates.terminal(item.raw_record_id)
        history = uow.decisions.for_review(item.id)
        previous = history[-1] if history else None
        if (
            command.candidate_revision_id != candidate.id
            or classification.candidate_revision_id != candidate.id
            or command.expected_sequence != (previous.sequence if previous else 0)
            or command.supersedes_decision_id != (previous.id if previous else None)
        ):
            raise StaleDecisionError(
                "Candidate or decision history changed; refresh the review"
            )
        legal = legal_outcomes(classification.verdict)
        if command.outcome not in legal:
            raise IllegalDecisionError(
                "Outcome is not legal for this classified record"
            )
        if previous is not None and previous.outcome == command.outcome:
            raise IllegalDecisionError("A superseding decision must change the outcome")
        if command.outcome is DecisionOutcome.APPROVE and not _dependencies_ready(
            classification, uow
        ):
            raise IllegalDecisionError("Approval is waiting for canonical dependencies")
        withdrawal = None
        if previous is not None and previous.outcome is DecisionOutcome.APPROVE:
            revision = uow.canonicals.for_candidate(candidate.id)
            assert revision is not None
            key = business_key(candidate.payload)
            assert key is not None
            uow.canonicals.lock_key(*key)
            if uow.canonicals.current(revision.identity_id) != revision:
                raise StaleDecisionError(
                    "A later canonical revision is current; "
                    "this approval cannot be reversed"
                )
            activation = next(
                event
                for event in uow.canonicals.promotion_events(revision.identity_id)
                if event.decision_id == previous.id
                and event.action is PromotionAction.ACTIVATE
            )
            withdrawal = (revision, activation.prior_current_revision_id)
        decision = ReviewDecision(
            new_id(),
            item.id,
            candidate.id,
            command.expected_sequence + 1,
            command.outcome,
            command.operator_name,
            command.reason,
            command.idempotency_key,
            command.supersedes_decision_id,
            clock.now(),
        )
        uow.decisions.append(decision)
        canonical = None
        if command.outcome is DecisionOutcome.APPROVE:
            canonical = _promote(candidate, decision.id, uow, clock)
            _unblock_dependants(uow, clock)
        elif withdrawal is not None:
            revision, restore_id = withdrawal
            uow.canonicals.withdraw(
                CanonicalPromotionEvent(
                    new_id(),
                    revision.identity_id,
                    revision.id,
                    PromotionAction.WITHDRAW,
                    decision.id,
                    restore_id,
                    clock.now(),
                )
            )
        uow.commit()
        return DecisionResult(decision, canonical, decision.effective_state, False)
