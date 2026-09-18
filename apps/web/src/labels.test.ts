import { expect, test } from "vitest";
import { displayLabel } from "./labels";
test("unknown server codes remain readable when new states are introduced", () => {
  expect(displayLabel("waiting_for_source")).toBe("Waiting For Source");
  expect(displayLabel("NEEDS_REVIEW")).toBe("Needs attention");
});
