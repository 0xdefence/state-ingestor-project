import type { RunDetail } from "../../api/contracts";
import { displayLabel } from "../../labels";
import { Value } from "./EvidencePanel";
export const stageOrder = ["parse", "normalise", "classify", "load"] as const;
export type Stage = (typeof stageOrder)[number];
const stageStates = [
  ["parsing", "parsed"],
  ["normalising", "normalised"],
  ["classifying", "classified"],
  ["loading", "staged"],
];
export function defaultStage(detail: RunDetail): Stage {
  const failure = detail.run.stage_failure?.replace("_failed", "");
  if (stageOrder.includes(failure as Stage)) return failure as Stage;
  const index = stageStates.findIndex((states) =>
    states.includes(detail.run.state),
  );
  return stageOrder[Math.max(0, index)];
}
export function PipelineStages({
  detail,
  selected,
  onSelect,
}: {
  detail: RunDetail;
  selected: Stage;
  onSelect: (stage: Stage) => void;
}) {
  const current = stageOrder.indexOf(defaultStage(detail));
  return (
    <section className="pipeline">
      <h2>Processing stages</h2>
      <div className="stage-controls">
        {stageOrder.map((stage, i) => {
          const hasEvidence =
            detail.events.some((e) => e.stage === stage) ||
            detail.checkpoints.some((c) => c.stage === stage);
          const reached =
            i <= current &&
            detail.run.state !== "ingested" &&
            detail.run.state !== "created";
          return (
            <button
              key={stage}
              type="button"
              aria-pressed={stage === selected}
              disabled={!hasEvidence}
              onClick={() => onSelect(stage)}
            >
              {displayLabel(stage)}
              {!hasEvidence && (
                <small>{reached ? "No details yet" : "Not reached"}</small>
              )}
            </button>
          );
        })}
      </div>
      <section
        aria-label="Stage evidence"
        className="stage-evidence"
        aria-live="polite"
      >
        <h3>{displayLabel(selected)}</h3>
        {detail.events
          .filter((e) => e.stage === selected)
          .map((event, i) => (
            <div className="evidence-entry" key={String(event.id ?? i)}>
              <p>{displayLabel(String(event.event_type))}</p>
              <Value value={event.occurred_at} />
              <Value value={event.facts} />
            </div>
          ))}
        {detail.checkpoints
          .filter((c) => c.stage === selected)
          .map((checkpoint, i) => (
            <div key={i}>
              <h3>Saved progress</h3>
              <Value value={checkpoint} />
            </div>
          ))}
        {!detail.events.some((e) => e.stage === selected) &&
          !detail.checkpoints.some((c) => c.stage === selected) && (
            <p>No stage evidence is available yet.</p>
          )}
      </section>
    </section>
  );
}
