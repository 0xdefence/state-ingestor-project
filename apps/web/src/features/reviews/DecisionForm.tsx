import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type {
  DecisionCommand,
  DecisionOutcome,
  ReviewDetailView,
} from "../../api/contracts";
import { ApiError } from "../../api/client";
import { saveDecision } from "../../api/queries";
import { displayLabel } from "../../labels";
export function DecisionForm({
  detail,
  disabled = false,
}: {
  detail: ReviewDetailView;
  disabled?: boolean;
}) {
  const client = useQueryClient();
  const [operator, setOperator] = useState("");
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState({ operator: false, reason: false });
  const [message, setMessage] = useState("");
  const attempt = useRef<{ fingerprint: string; key: string } | null>(null);
  const operatorInput = useRef<HTMLInputElement>(null);
  const reasonInput = useRef<HTMLTextAreaElement>(null);
  const [savingOutcome, setSavingOutcome] = useState<DecisionOutcome | null>(
    null,
  );
  const mutation = useMutation({
    mutationFn: (command: DecisionCommand) =>
      saveDecision(detail.item.id, command),
    onSuccess: async (result) => {
      attempt.current = null;
      setMessage(
        `Decision saved. ${displayLabel(result.effective_state)}.${result.canonical_revision ? ` Canonical revision ${result.canonical_revision.revision_number}.` : ""}${detail.item.latest_decision_id ? " Previous decision superseded." : ""}`,
      );
      await Promise.all(
        ["workspace", "run", "reviews", "review"].map((key) =>
          client.invalidateQueries({ queryKey: [key] }),
        ),
      );
    },
    onError: async (error) => {
      if (error instanceof ApiError && error.status === 409) {
        attempt.current = null;
        setMessage(
          "This record changed before your decision was saved. Review the refreshed evidence and try again.",
        );
        await client.invalidateQueries({
          queryKey: ["review", detail.item.id],
        });
        await client.invalidateQueries({ queryKey: ["reviews"] });
      } else
        setMessage(
          `Decision could not be confirmed. Your input is preserved. ${error.message}`,
        );
    },
  });
  const outcomes = detail.allowed_outcomes.filter(
    (o) => o !== "approve" || detail.item.readiness !== "blocked_by_dependency",
  );
  function submit(outcome: DecisionOutcome) {
    if (mutation.isPending || disabled) return;
    const nextErrors = {
      operator: !operator.trim(),
      reason: outcome === "reject" && !reason.trim(),
    };
    setErrors(nextErrors);
    if (nextErrors.operator) {
      operatorInput.current?.focus();
      return;
    }
    if (nextErrors.reason) {
      reasonInput.current?.focus();
      return;
    }
    const body = {
      candidate_revision_id: detail.item.candidate_revision_id,
      expected_sequence: detail.item.decision_sequence,
      outcome,
      operator_name: operator.trim(),
      reason: reason.trim() || null,
      supersedes_decision_id: detail.item.latest_decision_id,
    };
    const fingerprint = JSON.stringify(body);
    if (attempt.current?.fingerprint !== fingerprint)
      attempt.current = { fingerprint, key: crypto.randomUUID() };
    setMessage("");
    setSavingOutcome(outcome);
    mutation.mutate({ ...body, idempotency_key: attempt.current.key });
  }
  return (
    <section className="decision-form">
      <h3>Record a decision</h3>
      <p>
        This decision applies to the whole record.
        {detail.item.latest_decision_id
          ? " A new decision supersedes the previous outcome."
          : ""}
      </p>
      {message && (
        <p
          role={mutation.isError ? "alert" : "status"}
          className={mutation.isError ? "error-message" : "notice"}
        >
          {message}
        </p>
      )}
      {outcomes.length ? (
        <form noValidate onSubmit={(e) => e.preventDefault()}>
          <fieldset disabled={mutation.isPending || disabled}>
            <div className="decision-inputs">
              <label>
                Operator name
                <input
                  ref={operatorInput}
                  required
                  value={operator}
                  onChange={(e) => setOperator(e.target.value)}
                  aria-invalid={errors.operator}
                  aria-describedby={
                    errors.operator ? "operator-error" : undefined
                  }
                />
              </label>
              {errors.operator && (
                <p id="operator-error" className="error-message">
                  Enter the operator name.
                </p>
              )}
              <label>
                Reason
                <textarea
                  ref={reasonInput}
                  rows={3}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  aria-invalid={errors.reason}
                  aria-describedby={
                    errors.reason ? "reason-error" : "reason-help"
                  }
                />
              </label>
              <p id="reason-help">
                Required when rejecting. Optional for other decisions.
              </p>
              {errors.reason && (
                <p id="reason-error" className="error-message">
                  Enter a reason for rejection.
                </p>
              )}
            </div>
            <div className="decision-actions">
              {outcomes.map((outcome) => (
                <button
                  className={outcome === "approve" ? "primary" : undefined}
                  key={outcome}
                  type="button"
                  onClick={() => submit(outcome)}
                >
                  {mutation.isPending && savingOutcome === outcome
                    ? "Saving…"
                    : displayLabel(outcome)}
                </button>
              ))}
            </div>
          </fieldset>
        </form>
      ) : (
        <p>No manual decision is available for this record.</p>
      )}
    </section>
  );
}
