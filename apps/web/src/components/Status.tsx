import { displayLabel, statusTone } from "../labels";
export function Status({ value }: { value: string }) {
  return (
    <span className="status">
      <span aria-hidden="true" className={`dot tone-${statusTone(value)}`} />
      {displayLabel(value)}
    </span>
  );
}
