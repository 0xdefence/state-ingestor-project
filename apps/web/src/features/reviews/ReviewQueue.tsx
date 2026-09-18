import { useRef } from "react";
import { Link } from "react-router-dom";
import type { ReviewRow, RunView } from "../../api/contracts";
import { Status } from "../../components/Status";
export function ReviewQueue({
  items,
  runs,
  selected,
  onSelect,
}: {
  items: ReviewRow[];
  runs: RunView[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  return (
    <section
      className="panel review-queue"
      aria-label="Review queue"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.target instanceof HTMLAnchorElement) return;
        if (
          !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) ||
          !items.length
        )
          return;
        event.preventDefault();
        const index = buttons.current.indexOf(
          document.activeElement as HTMLButtonElement,
        );
        const next =
          event.key === "Home"
            ? 0
            : event.key === "End"
              ? items.length - 1
              : event.key === "ArrowDown"
                ? Math.min(items.length - 1, index + 1)
                : Math.max(0, index < 0 ? items.length - 1 : index - 1);
        buttons.current[next]?.focus();
      }}
    >
      <h2>Records for review</h2>
      <p className="small">
        Use ↑ and ↓ to move between records, then Enter to inspect.
      </p>
      <ul>
        {items.map((item, i) => (
          <li
            key={item.id}
            className={selected === item.id ? "selected-review" : undefined}
          >
            <button
              ref={(el) => {
                buttons.current[i] = el;
              }}
              aria-pressed={selected === item.id}
              onClick={() => onSelect(item.id)}
            >
              <strong>
                {item.business_identifier ?? "Business identifier unavailable"}
              </strong>
              <span className="queue-reason">
                {item.reason_summaries.join("; ")}
              </span>
              <Status
                value={
                  item.readiness === "blocked_by_dependency" &&
                  item.effective_state === "pending"
                    ? item.readiness
                    : item.effective_state
                }
              />
            </button>
            <p>
              <Link to={`/runs/${item.run_id}?review=${item.id}`}>
                {runs.find((r) => r.id === item.run_id)?.filename ??
                  "View source file"}
              </Link>{" "}
              · Line {item.source_line_start}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}
