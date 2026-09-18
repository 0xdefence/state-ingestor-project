import { Link } from "react-router-dom";
import type { RunView } from "../../api/contracts";
import { Status } from "../../components/Status";
import { AbsoluteTime } from "../../components/AbsoluteTime";
import { displayLabel } from "../../labels";
export function RecentRuns({
  runs,
  loading = false,
  failed = false,
  emptyMessage = "No files uploaded yet.",
}: {
  runs: RunView[];
  loading?: boolean;
  failed?: boolean;
  emptyMessage?: string;
}) {
  return (
    <section className="panel recent-panel" aria-labelledby="recent-title">
      <div className="section-heading">
        <h2 id="recent-title">Recent runs</h2>
        <div className="color-key" aria-label="Status key">
          {[
            "staged",
            "NEEDS_REVIEW",
            "blocked_by_dependency",
            "REJECTED",
            "load_failed",
          ].map((value) => (
            <Status key={value} value={value} />
          ))}
        </div>
      </div>
      {loading ? (
        <p>Loading runs…</p>
      ) : failed ? (
        <p>Recent runs are unavailable.</p>
      ) : runs.length === 0 ? (
        <p>{emptyMessage}</p>
      ) : (
        <div
          className="table-scroll"
          role="region"
          aria-label="Recent runs table"
          tabIndex={0}
        >
          <table>
            <thead>
              <tr>
                <th scope="col">File</th>
                <th scope="col">Status</th>
                <th scope="col">Outcomes</th>
                <th scope="col">Processed</th>
                <th scope="col">
                  <span className="sr-only">Navigation</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {[...runs]
                .sort(
                  (a, b) =>
                    b.created_at.instant.localeCompare(a.created_at.instant) ||
                    b.id.localeCompare(a.id),
                )
                .slice(0, 10)
                .map((run) => (
                  <tr key={run.id}>
                    <th scope="row">
                      <Link to={`/runs/${run.id}`}>
                        {run.filename ?? "Untitled file"}
                      </Link>
                    </th>
                    <td>
                      <Status value={run.stage_failure ?? run.state} />
                    </td>
                    <td>
                      <div className="outcome-segments">
                        {Object.entries(run.counts)
                          .filter(([, count]) => count > 0)
                          .map(([key, count]) => (
                            <span key={key}>
                              {count} {displayLabel(key)}
                            </span>
                          ))}
                        {Object.keys(run.counts).length === 0 && (
                          <span>Not classified yet</span>
                        )}
                      </div>
                    </td>
                    <td>
                      {run.processed_at ? (
                        <AbsoluteTime value={run.processed_at} />
                      ) : (
                        <span className="muted">
                          {run.state === "staged"
                            ? "Completion time unavailable"
                            : "Not processed yet"}
                        </span>
                      )}
                    </td>
                    <td>
                      <Link
                        to={`/runs/${run.id}`}
                        aria-label={`Open run for ${run.filename ?? run.id}`}
                      >
                        Open
                      </Link>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
