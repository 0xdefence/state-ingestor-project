import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { processRun } from "../../api/client";
import { useRun, useReviews } from "../../api/queries";
import { AbsoluteTime } from "../../components/AbsoluteTime";
import { Status } from "../../components/Status";
import { displayLabel } from "../../labels";
import { OutcomeBreakdown } from "../workspace/OutcomeBreakdown";
import { ReviewDetail } from "../reviews/ReviewDetail";
import { ReviewQueue } from "../reviews/ReviewQueue";
import { OccurrenceHistory } from "./OccurrenceHistory";
import {
  defaultStage,
  PipelineStages,
  stageOrder,
  type Stage,
} from "./PipelineStages";
import { EvidencePanel, recordEvidence, Value } from "./EvidencePanel";
export function RunPage() {
  const { runId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const query = useRun(runId);
  const scope = { kind: "current" as const, run_ids: [runId] };
  const reviews = useReviews(scope);
  const client = useQueryClient();
  const [processError, setProcessError] = useState("");
  const processing = useMutation({
    mutationFn: () => processRun(runId),
    onSuccess: async () => {
      setProcessError("");
      await Promise.all(
        ["run", "reviews", "workspace"].map((key) =>
          client.invalidateQueries({ queryKey: [key] }),
        ),
      );
    },
    onError: async (error) => {
      setProcessError(error.message);
      await client.invalidateQueries({ queryKey: ["run", runId] });
    },
  });
  const change = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    if (key === "record") next.delete("review");
    setParams(next);
  };
  if (query.isPending)
    return (
      <main className="workspace">
        <h1>Run</h1>
        <section role="status" className="panel">
          <h2>Loading run…</h2>
          <div className="skeleton" aria-hidden="true" />
        </section>
      </main>
    );
  if (!query.data)
    return (
      <main className="workspace">
        <h1>Run</h1>
        <Link to="/">Workspace</Link>
        <section role="alert" className="panel error-message">
          <h2>Run could not be loaded</h2>
          <p>{query.error?.message}</p>
          <button onClick={() => void query.refetch()}>
            Retry loading run
          </button>
        </section>
      </main>
    );
  const detail = query.data;
  const run = detail.run;
  const selected = params.get("review");
  const stage = stageOrder.includes(params.get("stage") as Stage)
    ? (params.get("stage") as Stage)
    : defaultStage(detail);
  const record = detail.records?.find((r) => r.id === params.get("record"));
  const dataCount = detail.records?.length
    ? detail.records.filter((r) => r.kind === "data").length
    : Object.values(run.counts).reduce((a, b) => a + b, 0);
  return (
    <main className="workspace run-page">
      <header className="page-heading">
        <Link to="/">Workspace</Link>
        <h1>{run.filename ?? "Unnamed file"}</h1>
        <p className="identifier">{run.id}</p>
        <div className="run-summary">
          <Status value={run.stage_failure ?? run.state} />
          <span>
            {dataCount} data records
            {run.state !== "staged" ? " saved so far" : ""}
          </span>
          {run.processed_at ? (
            <AbsoluteTime value={run.processed_at} />
          ) : (
            <span className="muted">
              {run.state === "staged"
                ? "Completion time unavailable"
                : "Not processed yet"}
            </span>
          )}
        </div>
      </header>
      {query.isFetching && <p role="status">Refreshing run evidence…</p>}
      {query.isError && (
        <div role="alert" className="error-message">
          <p>Run evidence could not be refreshed. {query.error.message}</p>
          <button onClick={() => void query.refetch()}>
            Retry refreshing run
          </button>
        </div>
      )}
      {run.state !== "staged" && (
        <section className="panel processing-panel">
          <h2>
            {run.stage_failure
              ? "Processing stopped"
              : "Processing is incomplete"}
          </h2>
          <p>
            {run.stage_failure
              ? "Saved records and checkpoints are preserved. Retry to continue from the durable boundary."
              : "The run projection refreshes while processing continues. You can resume processing here."}
          </p>
          <button
            className="primary"
            disabled={processing.isPending}
            onClick={() => processing.mutate()}
          >
            {processing.isPending
              ? "Processing…"
              : run.stage_failure
                ? "Retry processing"
                : "Process file"}
          </button>
          {processError && (
            <p role="alert" className="error-message">
              {processError}
            </p>
          )}
        </section>
      )}
      <div className="run-overview">
        <section className="panel">
          <OccurrenceHistory occurrences={detail.occurrences} />
          <PipelineStages
            detail={detail}
            selected={stage}
            onSelect={(s) => change("stage", s)}
          />
          <details>
            <summary>Run provenance</summary>
            <Value
              value={{
                source_file_id: run.source_file_id,
                source_sha256: run.source_sha256,
                source_byte_size: run.source_byte_size,
                fx_snapshot_id: run.fx_snapshot_id,
                rules_version: run.rules_version,
                build_revision: run.build_revision,
                predecessor_run_id: run.predecessor_run_id,
              }}
            />
          </details>
        </section>
        <OutcomeBreakdown scope={scope} runs={[run]} />
      </div>
      <section className="panel run-records">
        <h2>All records</h2>
        {detail.records?.length ? (
          <div
            className="table-scroll"
            role="region"
            aria-label="Run records"
            tabIndex={0}
          >
            <table>
              <thead>
                <tr>
                  <th>Source lines</th>
                  <th>Record</th>
                  <th>Classification</th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {detail.records.map((r) => (
                  <tr key={r.id}>
                    <th scope="row">
                      {r.source_line_start === r.source_line_end
                        ? r.source_line_start
                        : `${r.source_line_start}–${r.source_line_end}`}
                    </th>
                    <td>{r.business_identifier ?? displayLabel(r.kind)}</td>
                    <td>
                      {r.verdict ? (
                        <Status value={r.verdict} />
                      ) : (
                        "Not classified"
                      )}
                    </td>
                    <td>
                      <button
                        aria-pressed={
                          record?.id === r.id ||
                          !!(r.review_item_id && selected === r.review_item_id)
                        }
                        onClick={() =>
                          change(
                            r.review_item_id ? "review" : "record",
                            r.review_item_id ?? r.id,
                          )
                        }
                      >
                        Inspect line {r.source_line_start}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p>No parsed records are available yet.</p>
        )}
      </section>
      <div className="review-layout">
        <div>
          {reviews.isPending ? (
            <p role="status">Loading run reviews…</p>
          ) : reviews.isError ? (
            <div role="alert" className="panel error-message">
              <h2>Run reviews could not be loaded</h2>
              <p>{reviews.error.message}</p>
              <button onClick={() => void reviews.refetch()}>
                Retry loading reviews
              </button>
            </div>
          ) : reviews.data.items.length ? (
            <ReviewQueue
              items={reviews.data.items}
              runs={[run]}
              selected={selected}
              onSelect={(id) => change("review", id)}
            />
          ) : (
            <section className="panel">
              <h2>Records for review</h2>
              <p>No records need review in this run.</p>
            </section>
          )}
        </div>
        {selected ? (
          <ReviewDetail key={selected} id={selected} />
        ) : record ? (
          <section className="panel" aria-live="polite">
            <h2>
              Record{" "}
              {record.business_identifier ??
                `at line ${record.source_line_start}`}
            </h2>
            <EvidencePanel
              evidence={recordEvidence(detail.evidence, record.id)}
              rawId={record.id}
              candidateId={record.candidate_revision_id}
            />
          </section>
        ) : (
          <section className="panel empty-detail">
            <h2>Select a record</h2>
            <p>
              Inspect any source record or select an item that needs review.
            </p>
          </section>
        )}
      </div>
    </main>
  );
}
