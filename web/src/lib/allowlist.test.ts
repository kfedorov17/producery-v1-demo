import { describe, expect, it } from "vitest";
import { isAllowed, isFeedback, isResume, timeoutFor } from "./allowlist";

describe("proxy allowlist", () => {
  it("admits exactly the producer routes", () => {
    for (const [m, p] of [
      ["GET", "inbox"], ["POST", "inbox"], ["GET", "proposals"], ["GET", "outbox"], ["GET", "book/v_renewals"],
      ["GET", "documents"], ["POST", "documents"], ["GET", "documents/search"], ["GET", "connections"],
      ["PUT", "connections/onedrive"], ["GET", "threads/crew:x"], ["GET", "threads/crew:x/audit"],
      ["GET", "threads/crew:x/history"], ["POST", "threads/crew:x/resume"], ["POST", "threads/crew:x/feedback"],
      ["POST", "threads/crew:x/continue"], ["GET", "agents"], ["GET", "ready"],
    ] as const) expect(isAllowed(m, p), `${m} ${p}`).toBe(true);
  });
  it("refuses everything that starts a run, writes state, or reaches a raw table", () => {
    for (const [m, p] of [
      ["POST", "runs"], ["DELETE", "inbox"], ["GET", "book/households"], ["GET", "book/inbox"], ["PUT", "connections/phone"],
      ["POST", "threads/crew:x"], ["GET", "threads/crew:x/resume"], ["GET", "health"], ["GET", "threads"], ["POST", "connections/email"],
      ["GET", "threads/eval:crew-001"], ["POST", "threads/smoke:x/resume"], ["GET", "threads/eval:x/audit"],
    ] as const) expect(isAllowed(m, p), `${m} ${p}`).toBe(false);
  });
  it("knows a resume and gives a continue the longest timeout", () => {
    expect(isResume("threads/crew:x/resume")).toBe(true);
    expect(isResume("threads/crew:x/continue")).toBe(false);
    expect(isFeedback("threads/crew:x/feedback")).toBe(true);
    expect(isFeedback("threads/crew:x/resume")).toBe(false);
    expect(timeoutFor("threads/crew:x/continue")).toBeGreaterThan(timeoutFor("threads/crew:x/resume"));
    expect(timeoutFor("inbox")).toBe(30_000);
  });
});
