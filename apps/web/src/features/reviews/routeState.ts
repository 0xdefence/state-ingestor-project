import { verdictOrder } from "../../labels";
const states = ["pending", "approved", "rejected", "acknowledged"];
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function normalizedReviewParams(input: URLSearchParams) {
  const params = new URLSearchParams(input);
  let kind = params.get("scope") ?? "all";
  if (!["all", "current", "selected"].includes(kind)) {
    kind = "all";
    params.set("scope", kind);
  }
  const ids = [
    ...new Set(params.getAll("run_id").filter((id) => uuid.test(id))),
  ];
  if (kind === "current" && ids.length > 1) {
    kind = "selected";
    params.set("scope", kind);
  }
  params.delete("run_id");
  if (kind !== "all") ids.forEach((id) => params.append("run_id", id));
  const state = params.get("state") ?? params.get("effective_state");
  params.delete("effective_state");
  if (state && states.includes(state)) params.set("state", state);
  else params.delete("state");
  if (
    !verdictOrder.includes(
      params.get("verdict") as (typeof verdictOrder)[number],
    )
  )
    params.delete("verdict");
  return params;
}
