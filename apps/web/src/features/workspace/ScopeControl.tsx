import { useState } from "react";
import type { FileScope, RunView, ScopeKind } from "../../api/contracts";
import { displayLabel } from "../../labels";
export function ScopeControl({
  scope,
  currentRunId,
  runs,
  selectedRuns,
  catalogStatus,
  catalogError,
  catalogFetching,
  onRetryCatalog,
  onChange,
}: {
  scope: FileScope;
  currentRunId: string | null;
  runs: RunView[];
  selectedRuns: RunView[];
  catalogStatus: "pending" | "error" | "success";
  catalogError: Error | null;
  catalogFetching: boolean;
  onRetryCatalog: () => void;
  onChange: (scope: FileScope) => void;
}) {
  const [search, setSearch] = useState("");
  const filename = (id: string) =>
    runs.find((run) => run.id === id)?.filename ??
    selectedRuns.find((run) => run.id === id)?.filename ??
    id;
  const remove = (id: string) =>
    onChange({
      ...scope,
      run_ids: scope.run_ids.filter((value) => value !== id),
    });
  return (
    <div className="scope-controls">
      <label className="scope-label">
        File scope
        <select
          value={scope.kind}
          onChange={(e) => {
            const kind = e.target.value as ScopeKind;
            onChange({
              kind,
              run_ids: kind === "current" && currentRunId ? [currentRunId] : [],
            });
          }}
        >
          {(["current", "selected", "all"] as const).map((kind) => (
            <option key={kind} value={kind}>
              {displayLabel(kind)}
            </option>
          ))}
        </select>
      </label>
      {scope.kind === "selected" && (
        <section className="selection panel" aria-label="Selected files">
          <strong>
            {scope.run_ids.length} selected{" "}
            {scope.run_ids.length === 1 ? "file" : "files"}
          </strong>
          <div className="selected-files">
            {scope.run_ids.map((id) => (
              <button
                type="button"
                key={id}
                className="file-chip"
                aria-label={`Remove ${filename(id)}`}
                onClick={() => remove(id)}
              >
                {filename(id)}
                <span>Remove</span>
              </button>
            ))}
          </div>
          <details>
            <summary>Choose files</summary>
            <label>
              Search files
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
            {catalogStatus === "pending" && <p role="status">Loading files…</p>}
            {catalogStatus === "error" && (
              <div role="alert" className="error-message">
                <p>Files could not be loaded. {catalogError?.message}</p>
                <button
                  type="button"
                  disabled={catalogFetching}
                  onClick={onRetryCatalog}
                >
                  Retry loading files
                </button>
              </div>
            )}
            <div className="file-options">
              {runs
                .filter((run) =>
                  (run.filename ?? run.id)
                    .toLowerCase()
                    .includes(search.toLowerCase()),
                )
                .map((run) => (
                  <label key={run.id}>
                    <input
                      type="checkbox"
                      checked={scope.run_ids.includes(run.id)}
                      onChange={(e) =>
                        e.target.checked
                          ? onChange({
                              ...scope,
                              run_ids: [...scope.run_ids, run.id],
                            })
                          : remove(run.id)
                      }
                    />
                    <span>{run.filename ?? run.id}</span>
                  </label>
                ))}
            </div>
            {catalogStatus === "success" &&
              !runs.some((run) =>
                (run.filename ?? run.id)
                  .toLowerCase()
                  .includes(search.toLowerCase()),
              ) && <p>No matching files.</p>}
          </details>
        </section>
      )}
    </div>
  );
}
