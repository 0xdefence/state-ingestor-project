import type { RunDetail } from "../../api/contracts";
import { Value } from "./EvidencePanel";
export function OccurrenceHistory({
  occurrences,
}: {
  occurrences: RunDetail["occurrences"];
}) {
  return (
    <section className="occurrences">
      <h2>Submission history</h2>
      {occurrences.length > 1 && (
        <p>
          These exact file contents were submitted more than once. Completed
          processing was reused.
        </p>
      )}
      <details>
        <summary>
          {occurrences.length} exact{" "}
          {occurrences.length === 1 ? "submission" : "submissions"}
        </summary>
        <ol>
          {occurrences.map((o, i) => (
            <li key={String(o.id ?? i)}>
              <strong>{String(o.filename)}</strong>
              <p>{String(o.actor_label)}</p>
              <Value value={o.ingested_at} />
            </li>
          ))}
        </ol>
      </details>
    </section>
  );
}
