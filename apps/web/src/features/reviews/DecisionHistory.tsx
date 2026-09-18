import type { ReviewDetailView } from "../../api/contracts";
import { AbsoluteTime } from "../../components/AbsoluteTime";
import { displayLabel } from "../../labels";
export function DecisionHistory({
  decisions,
}: {
  decisions: ReviewDetailView["decisions"];
}) {
  return (
    <section className="decision-history">
      <h3>Decision history</h3>
      {decisions.length ? (
        <ol>
          {[...decisions]
            .sort((a, b) => a.decision.sequence - b.decision.sequence)
            .map(({ decision: d }) => (
              <li key={d.id}>
                <strong>
                  {d.sequence}. {displayLabel(d.outcome)} · {d.operator_name}
                </strong>
                <p>
                  <AbsoluteTime value={d.decided_at} />
                </p>
                {d.reason && <p>{d.reason}</p>}
                {d.supersedes_decision_id && (
                  <p>Reverses the previous decision.</p>
                )}
                <details>
                  <summary>Decision references</summary>
                  <p className="identifier">Decision: {d.id}</p>
                  <p className="identifier">
                    Candidate: {d.candidate_revision_id}
                  </p>
                  {d.supersedes_decision_id && (
                    <p className="identifier">
                      Supersedes: {d.supersedes_decision_id}
                    </p>
                  )}
                </details>
              </li>
            ))}
        </ol>
      ) : (
        <p>No decisions recorded yet.</p>
      )}
    </section>
  );
}
