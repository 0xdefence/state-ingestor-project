import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { FileScope } from "../../api/contracts";
import { scopeParams, useReviews, useWorkspace } from "../../api/queries";
import { displayLabel, verdictOrder } from "../../labels";
import { ScopeControl } from "../workspace/ScopeControl";
import { ReviewQueue } from "./ReviewQueue";
import { ReviewDetail } from "./ReviewDetail";
import { normalizedReviewParams } from "./routeState";
export function ReviewPage() {
  const [rawParams, setParams] = useSearchParams();
  const params = normalizedReviewParams(rawParams);
  const [adjusted, setAdjusted] = useState(false);
  const rawSorted = new URLSearchParams(rawParams);
  rawSorted.sort();
  const normalizedSorted = new URLSearchParams(params);
  normalizedSorted.sort();
  const malformed = normalizedSorted.toString() !== rawSorted.toString();
  useEffect(() => {
    if (malformed) {
      setAdjusted(true);
      setParams(params, { replace: true });
    }
  }, [rawParams.toString()]);
  const kind = params.get("scope");
  const scope: FileScope = {
    kind: kind === "current" || kind === "selected" ? kind : "all",
    run_ids:
      kind === "current" || kind === "selected"
        ? [...new Set(params.getAll("run_id"))]
        : [],
  };
  const currentRun = useRef<string | null>(null);
  if (scope.kind === "current" && scope.run_ids.length === 1)
    currentRun.current = scope.run_ids[0];
  const state = params.get("state") ?? "";
  const verdict = params.get("verdict") ?? "";
  const selected = params.get("review");
  const queue = useReviews(scope, state, verdict);
  const catalog = useWorkspace({ kind: "all", run_ids: [] });
  const unselected = scope.kind !== "all" && !scope.run_ids.length;
  const change = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  };
  return (
    <main className="workspace">
      <header className="page-heading">
        <Link to="/">Workspace</Link>
        <h1>Review records</h1>
        <p>Inspect the evidence and record a whole-record decision.</p>
      </header>
      {adjusted && (
        <p role="status" className="notice">
          Some URL filters were invalid and have been adjusted.
        </p>
      )}
      <ScopeControl
        scope={scope}
        currentRunId={currentRun.current}
        runs={catalog.data?.runs ?? []}
        selectedRuns={catalog.data?.runs ?? []}
        catalogStatus={catalog.status}
        catalogError={catalog.error}
        catalogFetching={catalog.isFetching}
        onRetryCatalog={() => void catalog.refetch()}
        onChange={(next) => {
          const query = scopeParams(next);
          for (const key of ["state", "verdict", "review"]) {
            const value = params.get(key);
            if (value) query.set(key, value);
          }
          setParams(query);
        }}
      />
      <div className="review-filters">
        <label>
          Review state
          <select
            value={state}
            onChange={(e) => change("state", e.target.value)}
          >
            <option value="">All review states</option>
            {["pending", "approved", "rejected", "acknowledged"].map((s) => (
              <option value={s} key={s}>
                {displayLabel(s)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Classification
          <select
            value={verdict}
            onChange={(e) => change("verdict", e.target.value)}
          >
            <option value="">All classifications</option>
            {verdictOrder.map((v) => (
              <option value={v} key={v}>
                {displayLabel(v)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="review-layout">
        <div>
          {unselected ? (
            <div className="panel">
              <p>
                {scope.kind === "current"
                  ? "No current file is open."
                  : "Choose files to see records for review."}
              </p>
              <button
                onClick={() => {
                  const next = new URLSearchParams(params);
                  next.set("scope", "selected");
                  setParams(next);
                }}
              >
                Choose a file
              </button>
            </div>
          ) : queue.isPending ? (
            <section className="panel" role="status">
              <h2>Loading review queue…</h2>
              <div className="skeleton" aria-hidden="true" />
            </section>
          ) : queue.isError ? (
            <section className="panel error-message" role="alert">
              <h2>Review queue could not be loaded</h2>
              <p>{queue.error.message}</p>
              <button onClick={() => void queue.refetch()}>
                Retry loading queue
              </button>
            </section>
          ) : queue.data.items.length ? (
            <>
              <ReviewQueue
                items={queue.data.items}
                runs={catalog.data?.runs ?? []}
                selected={selected}
                onSelect={(id) => change("review", id)}
              />
              {queue.isFetching && (
                <p role="status">Refreshing review queue…</p>
              )}
            </>
          ) : (
            <section className="panel">
              <h2>Records for review</h2>
              <p>No records match these filters.</p>
            </section>
          )}
        </div>
        {selected ? (
          <ReviewDetail key={selected} id={selected} scope={scope} />
        ) : (
          <section className="panel empty-detail">
            <h2>Select a record</h2>
            <p>
              Choose a record in the queue to inspect its source values and
              available decisions.
            </p>
          </section>
        )}
      </div>
    </main>
  );
}
