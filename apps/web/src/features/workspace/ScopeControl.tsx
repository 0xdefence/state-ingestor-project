import { useState } from "react";
import type { FileScope, RunView, ScopeKind } from "../../api/contracts";
import { displayLabel } from "../../labels";
export function ScopeControl({
  scope,
  runs,
  onChange,
}: {
  scope: FileScope;
  runs: RunView[];
  onChange: (scope: FileScope) => void;
}) {
  const [search, setSearch] = useState("");
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
              run_ids:
                kind === "all"
                  ? []
                  : kind === "current"
                    ? scope.run_ids[0]
                      ? [scope.run_ids[0]]
                      : runs[0]
                        ? [runs[0].id]
                        : []
                    : [],
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
                aria-label={`Remove ${runs.find((run) => run.id === id)?.filename ?? id}`}
                onClick={() => remove(id)}
              >
                {runs.find((run) => run.id === id)?.filename ?? id}
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
            {!runs.some((run) =>
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
