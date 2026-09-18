import { useSearchParams } from "react-router-dom";
import type { FileScope } from "../../api/contracts";
import { scopeParams, useReviews, useWorkspace } from "../../api/queries";
import { displayLabel } from "../../labels";
import { UploadPanel } from "./UploadPanel";
import { ScopeControl } from "./ScopeControl";
import { AttentionSummary } from "./AttentionSummary";
import { OutcomeBreakdown } from "./OutcomeBreakdown";
import { RecentRuns } from "./RecentRuns";
export function WorkspacePage() {
  const [params, setParams] = useSearchParams();
  const kind = params.get("scope");
  const scope: FileScope = {
    kind: kind === "current" || kind === "selected" ? kind : "all",
    run_ids:
      kind === "current" || kind === "selected"
        ? [...new Set(params.getAll("run_id"))]
        : [],
  };
  const catalog = useWorkspace({ kind: "all", run_ids: [] });
  const workspace = useWorkspace(scope);
  const reviews = useReviews(scope);
  const unselected = scope.kind !== "all" && scope.run_ids.length === 0;
  const error =
    catalog.error ?? (!unselected ? (workspace.error ?? reviews.error) : null);
  const loading =
    catalog.isPending ||
    (!unselected && (workspace.isPending || reviews.isPending));
  const runs = unselected ? [] : (workspace.data?.runs ?? []);
  const items = unselected ? [] : (reviews.data?.items ?? []);
  return (
    <main className="workspace">
      <header className="page-heading">
        <div>
          <h1>Workspace</h1>
          <p>
            {displayLabel(scope.kind)}
            {scope.kind === "current" && runs[0]?.filename
              ? ` · ${runs[0].filename}`
              : ""}
          </p>
        </div>
      </header>
      <UploadPanel />
      <ScopeControl
        scope={scope}
        runs={catalog.data?.runs ?? []}
        onChange={(next) => setParams(scopeParams(next))}
      />
      {error ? (
        <div role="alert" className="panel error-message">
          <h2>Workspace could not be loaded</h2>
          <p>{error.message}</p>
          <button
            onClick={() => {
              void catalog.refetch();
              if (!unselected) {
                void workspace.refetch();
                void reviews.refetch();
              }
            }}
          >
            Retry loading workspace
          </button>
        </div>
      ) : loading ? (
        <div className="workspace-loading" role="status">
          <span>Loading workspace…</span>
          <div className="attention-grid">
            <section className="panel skeleton">
              <h2>Outstanding review</h2>
            </section>
            <section className="panel skeleton">
              <h2>Outcome breakdown</h2>
            </section>
          </div>
          <div className="panel skeleton skeleton-table" aria-hidden="true" />
        </div>
      ) : (
        <>
          {unselected && (
            <p className="scope-prompt">
              {scope.kind === "selected"
                ? "Choose files to see their outcomes and outstanding review."
                : "Choose a current run from Recent runs, or use All files."}
            </p>
          )}
          <div className="attention-grid">
            <AttentionSummary scope={scope} runs={runs} items={items} />
            {!unselected && <OutcomeBreakdown scope={scope} runs={runs} />}
          </div>
        </>
      )}
      <RecentRuns
        runs={catalog.data?.runs ?? []}
        loading={catalog.isPending}
        failed={catalog.isError}
      />
    </main>
  );
}
