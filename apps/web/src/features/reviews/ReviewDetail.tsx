import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { useReview } from "../../api/queries";
import { Status } from "../../components/Status";
import { EvidencePanel, object, Value } from "../runs/EvidencePanel";
import { DecisionForm } from "./DecisionForm";
import { DecisionHistory } from "./DecisionHistory";
export function ReviewDetail({ id }: { id: string }) {
  const query = useReview(id);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (query.data) heading.current?.focus();
  }, [id, !!query.data]);
  if (query.isPending)
    return (
      <section className="panel detail-loading" role="status">
        <h2>Loading record…</h2>
        <div className="skeleton" aria-hidden="true" />
      </section>
    );
  if (!query.data)
    return (
      <section className="panel error-message" role="alert">
        <h2>Record could not be loaded</h2>
        <p>{query.error?.message}</p>
        <button onClick={() => void query.refetch()}>
          Retry loading record
        </button>
      </section>
    );
  const detail = query.data;
  const { item } = detail;
  const runNode = detail.evidence.nodes.find(
    (n) => n.kind === "run" && n.id === item.run_id,
  );
  const reasons = detail.evidence.nodes.find(
    (n) => n.kind === "review_item" && n.id === item.id,
  )?.attributes.reasons;
  return (
    <section className="panel review-detail" aria-labelledby="review-heading">
      <div className="section-heading">
        <h2 id="review-heading" ref={heading} tabIndex={-1}>
          Review{" "}
          {item.business_identifier ?? "record without a business identifier"}
        </h2>
        <Status value={item.effective_state} />
      </div>
      <p>
        <Link to={`/runs/${item.run_id}?review=${item.id}`}>
          View source file
        </Link>{" "}
        · Source{" "}
        {item.source_line_start === item.source_line_end
          ? `line ${item.source_line_start}`
          : `lines ${item.source_line_start}–${item.source_line_end}`}
      </p>
      <div className="detail-states">
        <Status value={item.verdict} />
        {item.readiness === "blocked_by_dependency" && (
          <Status value={item.readiness} />
        )}
      </div>
      <p>
        Pipeline position:{" "}
        {runNode ? (
          <Status value={String(runNode.attributes.state)} />
        ) : (
          "Classified record"
        )}
      </p>
      {query.isFetching && <p role="status">Refreshing evidence…</p>}
      {query.isError && (
        <div className="error-message" role="alert">
          <p>
            Evidence could not be refreshed. Decisions are paused until current
            evidence is available. {query.error.message}
          </p>
          <button onClick={() => void query.refetch()}>
            Retry refreshing evidence
          </button>
        </div>
      )}
      <section className="review-reasons">
        <h3>Why this record needs review</h3>
        <ul>
          {item.reason_summaries.map((reason, i) => (
            <li key={i}>{reason}</li>
          ))}
        </ul>
        {Array.isArray(reasons) && (
          <details>
            <summary>Registered reason references</summary>
            {reasons.map((reason, i) => (
              <Value key={i} value={object(reason)} />
            ))}
          </details>
        )}
      </section>
      <p className="small">Decision sequence: {item.decision_sequence}</p>
      <DecisionForm
        detail={detail}
        disabled={query.isError || query.isFetching}
      />
      <DecisionHistory decisions={detail.decisions} />
      <EvidencePanel
        evidence={detail.evidence}
        rawId={item.raw_record_id}
        candidateId={item.candidate_revision_id}
      />
    </section>
  );
}
