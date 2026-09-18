import type { AbsoluteInstant } from "../api/contracts";
export function AbsoluteTime({ value }: { value: AbsoluteInstant }) {
  return (
    <time
      dateTime={value.instant}
      title={`${value.instant} (${value.timezone})`}
    >
      {value.display}
    </time>
  );
}
