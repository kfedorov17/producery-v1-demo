import { describe, expect, it } from "vitest";
import { parseCitation } from "./citations";
import { citationsFromBrief } from "@/components/approval-card";

describe("parseCitation", () => {
  it("maps a document chunk to the Documents screen", () => {
    const c = parseCitation("[doc:doc-keystone-agreement#7]");
    expect(c.kind).toBe("doc");
    expect(c.label).toBe("doc-keystone-agreement#7");
    expect(c.href).toBe("/app/documents?q=doc-keystone-agreement");
  });
  it("maps a view column with a row id to Clients on that view", () => {
    const c = parseCitation("v_renewals.premium · HO-1001");
    expect(c.kind).toBe("view");
    expect(c.href).toBe("/app/clients?view=v_renewals&q=HO-1001");
  });
  it("leaves free text alone", () => {
    expect(parseCitation("client said so").kind).toBe("text");
  });
});

describe("citationsFromBrief", () => {
  it("pulls doc and view markers out of an owner brief", () => {
    const brief = "Renewal 2026-11-01 per v_renewals.x_date · HO-1001; roof rule in [doc:doc-uw-guide#3].";
    expect(citationsFromBrief(brief)).toEqual(["[doc:doc-uw-guide#3]", "v_renewals.x_date · HO-1001"]);
  });
  it("is empty for a missing brief", () => {
    expect(citationsFromBrief(null)).toEqual([]);
  });
});
