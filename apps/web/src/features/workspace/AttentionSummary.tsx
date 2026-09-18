import { Link } from "react-router-dom";
import type { FileScope, ReviewRow, RunView } from "../../api/contracts";
import { reviewHref } from "../../api/queries";
import { Status } from "../../components/Status";
export function AttentionSummary({
  scope,
  runs,
  items,
}: {
  scope: FileScope;
  runs: RunView[];
  items: ReviewRow[];
}) {
  const pending = items.filter(
    (item) =>
      item.effective_state === "pending" && item.current_status !== "promoted",
  );
  const failures = runs.filter((run) => run.stage_failure);
  const incomplete = runs.filter(
    (run) => run.state !== "staged" && !run.stage_failure,
  );
  return (
    <section
      className="panel attention-panel"
      aria-labelledby="attention-title"
    >
      <div className="section-heading">
        <h2 id="attention-title">Outstanding review</h2>
        {pending.length > 0 && (
          <Link to={reviewHref(scope, { effective_state: "pending" })}>
            Review {pending.length} outstanding{" "}
            {pending.length === 1 ? "record" : "records"}
          </Link>
        )}
      </div>
      {pending.length === 0 ? (
        <p>No outstanding review in this scope.</p>
      ) : (
        <ul className="attention-list">
          {pending.slice(0, 5).map((item) => (
            <li key={item.id}>
              <div>
                <Link
                  className="reason"
                  to={`${reviewHref(scope)}&review=${encodeURIComponent(item.id)}`}
                >
                  {item.reason_summaries[0] ?? "Review this record"}
                </Link>
                <p>
                  {item.business_identifier
                    ? `Business identifier: ${item.business_identifier}`
                    : "Business identifier unavailable"}
                </p>
                <p>
                  <Link
                    to={`/runs/${item.run_id}?review=${encodeURIComponent(item.id)}`}
                  >
                    {runs.find((run) => run.id === item.run_id)?.filename ??
                      "Open source file"}
                  </Link>
                  <span>
                    {" "}
                    · source line {item.source_line_start}
                    {item.source_line_end !== item.source_line_start
                      ? `–${item.source_line_end}`
                      : ""}
                  </span>
                </p>
              </div>
              <Status value={item.current_status} />
            </li>
          ))}
        </ul>
      )}
      <div className="processing-attention">
        <h3>Processing attention</h3>
        {failures.length === 0 && incomplete.length === 0 ? (
          <p>No failed or unfinished runs in this scope.</p>
        ) : (
          <ul>
            {[...failures, ...incomplete].map((run) => (
              <li key={run.id}>
                <Link to={`/runs/${run.id}`}>
                  {run.filename ?? "Untitled file"}
                </Link>
                <Status value={run.stage_failure ?? run.state} />
                <Link to={`/runs/${run.id}`}>Open run</Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
