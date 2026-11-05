import { describe, expect, it } from "vitest";
import { citedIds, notProposedItems, parseCitations, splitSummary } from "./citations";

describe("parseCitations", () => {
  it("splits text and single citations", () => {
    expect(parseCitations("Saves $50 [ev-03].")).toEqual([
      { type: "text", text: "Saves $50 " },
      { type: "cite", ids: ["ev-03"], raw: "[ev-03]" },
      { type: "text", text: "." },
    ]);
  });

  it("parses grouped citations with spaces", () => {
    const segs = parseCitations("a [ev-02, ev-17,ev-18] b");
    expect(segs[1]).toEqual({ type: "cite", ids: ["ev-02", "ev-17", "ev-18"], raw: "[ev-02, ev-17,ev-18]" });
  });

  it("leaves other brackets alone", () => {
    expect(parseCitations("see [docs] and [ev-x]")).toEqual([
      { type: "text", text: "see [docs] and [ev-x]" },
    ]);
  });

  it("handles empty and citation-only input", () => {
    expect(parseCitations("")).toEqual([]);
    expect(parseCitations(null)).toEqual([]);
    expect(parseCitations("[ev-01]")).toEqual([{ type: "cite", ids: ["ev-01"], raw: "[ev-01]" }]);
  });

  it("lists cited ids once, in order", () => {
    expect(citedIds("[ev-02] x [ev-01, ev-02] y [ev-03]")).toEqual(["ev-02", "ev-01", "ev-03"]);
  });
});

describe("splitSummary", () => {
  const summary =
    "Scope: idle VMs in dev. Proposing 2 change(s) [ev-01]. stop_vm i-1: idle; about $10/month. [ev-02, ev-03] delete_disk vol-1: unattached; about $5/month. [ev-04] Not proposed: i-5: protected [ev-05]; i-8: too new [ev-06].";

  it("puts each action and the exclusions on their own line", () => {
    expect(splitSummary(summary)).toEqual([
      "Scope: idle VMs in dev. Proposing 2 change(s) [ev-01].",
      "stop_vm i-1: idle; about $10/month. [ev-02, ev-03]",
      "delete_disk vol-1: unattached; about $5/month. [ev-04]",
      "Not proposed: i-5: protected [ev-05]; i-8: too new [ev-06].",
    ]);
  });

  it("splits the report at each action id", () => {
    expect(
      splitSummary("Executed 2. act-01 stop_vm a: verified [ev-1]. act-02 stop_vm b: verified [ev-2]."),
    ).toEqual(["Executed 2.", "act-01 stop_vm a: verified [ev-1].", "act-02 stop_vm b: verified [ev-2]."]);
  });

  it("never alters the text", () => {
    expect(splitSummary(summary).join(" ")).toBe(summary);
  });

  it("lists the exclusions", () => {
    expect(notProposedItems("Not proposed: i-5: protected [ev-05]; i-8: too new [ev-06].")).toEqual([
      "i-5: protected [ev-05]",
      "i-8: too new [ev-06]",
    ]);
    expect(notProposedItems("stop_vm i-1")).toBeNull();
  });
});
