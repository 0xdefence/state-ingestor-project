import { Link } from "react-router-dom";
import type { FileScope, RunView } from "../../api/contracts";
import { reviewHref } from "../../api/queries";
import { Status } from "../../components/Status";
import { displayLabel, verdictOrder } from "../../labels";
export function OutcomeBreakdown({
  scope,
  runs,
}: {
  scope: FileScope;
  runs: RunView[];
}) {
  const counts: Record<string, number> = {};
  runs.forEach((run) =>
    Object.entries(run.counts).forEach(([key, value]) => {
      counts[key] = (counts[key] ?? 0) + value;
    }),
  );
  const keys = [
    ...verdictOrder,
    ...Object.keys(counts).filter(
      (key) => !verdictOrder.includes(key as (typeof verdictOrder)[number]),
    ),
  ];
  const total = Object.values(counts).reduce((sum, count) => sum + count, 0);
  return (
    <section className="panel outcome-panel" aria-labelledby="outcome-title">
      <h2 id="outcome-title">Outcome breakdown</h2>
      <p>
        {displayLabel(scope.kind)} · {total} records
      </p>
      <ul className="outcome-list">
        {keys.map((key) => (
          <li key={key}>
            <Link
              to={reviewHref(scope, { verdict: key })}
              aria-label={`${counts[key] ?? 0} ${displayLabel(key)}`}
            >
              <Status value={key} />
              <strong>{counts[key] ?? 0}</strong>
            </Link>
          </li>
        ))}
      </ul>
      <p className="small">
        Classification outcomes. Review decisions are shown separately.
      </p>
    </section>
  );
}
