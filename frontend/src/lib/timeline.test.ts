import { describe, expect, it } from "vitest";
import {
  buildTimeline,
  emptyTimeline,
  pendingQuestionSeq,
  stepperModel,
  timelineReducer,
  visitedStatuses,
} from "./timeline";
import type { RunEvent } from "./types";

const ev = (seq: number, type: string, payload: Record<string, unknown> = {}): RunEvent => ({
  seq,
  at: "2026-11-01T00:00:00Z",
  type,
  payload,
});

describe("timelineReducer", () => {
  it("appends pages in order, dedupes by seq and tracks the cursor", () => {
    let s = timelineReducer(emptyTimeline, { type: "reset", runId: "r1" });
    s = timelineReducer(s, { type: "append", runId: "r1", events: [ev(2, "status"), ev(1, "created")] });
    expect(s.events.map((e) => e.seq)).toEqual([1, 2]);
    expect(s.lastSeq).toBe(2);
    s = timelineReducer(s, { type: "append", runId: "r1", events: [ev(2, "status"), ev(4, "model_turn")] });
    expect(s.events.map((e) => e.seq)).toEqual([1, 2, 4]);
    expect(s.lastSeq).toBe(4);
  });

  it("returns the same state when nothing is new, ignores other runs and spans", () => {
    const s = timelineReducer(timelineReducer(emptyTimeline, { type: "reset", runId: "r1" }), {
      type: "append",
      runId: "r1",
      events: [ev(1, "created")],
    });
    expect(timelineReducer(s, { type: "append", runId: "r1", events: [ev(1, "created")] })).toBe(s);
    expect(timelineReducer(s, { type: "append", runId: "r2", events: [ev(9, "status")] })).toBe(s);
    expect(timelineReducer(s, { type: "append", runId: "r1", events: [ev(5, "span")] })).toBe(s);
  });

  it("resets when the run changes", () => {
    const s = timelineReducer(emptyTimeline, { type: "reset", runId: "r2" });
    expect(s).toEqual({ runId: "r2", lastSeq: 0, events: [] });
  });
});

describe("buildTimeline", () => {
  it("folds a tool_error into its tool_call", () => {
    const items = buildTimeline([
      ev(1, "tool_call", { id: "t1", name: "get_utilization", ok: false }),
      ev(2, "tool_error", { id: "t1", kind: "timeout" }),
      ev(3, "tool_error", { id: "t9", kind: "validation" }),
    ]);
    expect(items.map((i) => i.type)).toEqual(["tool_call", "tool_error"]);
    expect(items[0]?.related.map((r) => r.seq)).toEqual([2]);
  });

  it("finds the question still waiting for an answer", () => {
    const events = [ev(1, "question", { text: "What?" })];
    expect(pendingQuestionSeq(events, "awaiting_input")).toBe(1);
    expect(pendingQuestionSeq([...events, ev(2, "answer")], "awaiting_input")).toBeNull();
    expect(pendingQuestionSeq(events, "planning")).toBeNull();
  });
});

describe("stepperModel", () => {
  const status = (from: string, to: string) => ev(0, "status", { from, to });

  it("marks the current phase while planning and waiting", () => {
    expect(stepperModel("planning", []).map((s) => s.state)).toEqual([
      "current",
      "pending",
      "pending",
      "pending",
      "pending",
    ]);
    const waiting = stepperModel("awaiting_approval", []);
    expect(waiting.map((s) => s.state)).toEqual(["done", "current", "pending", "pending", "pending"]);
    expect(stepperModel("awaiting_input", [])[0]?.note).toBe("Needs an answer");
  });

  it("completes every phase after execution", () => {
    const visited = visitedStatuses([
      status("planning", "awaiting_approval"),
      status("awaiting_approval", "executing"),
    ]);
    expect(stepperModel("completed", visited).every((s) => s.state === "done")).toBe(true);
    const partial = stepperModel("completed_with_failures", visited);
    expect(partial[4]).toMatchObject({ state: "warning", label: "Completed with failures" });
  });

  it("skips the middle phases when nothing was proposed", () => {
    const steps = stepperModel(
      "completed",
      visitedStatuses([status("created", "planning"), status("planning", "completed")]),
    );
    expect(steps.map((s) => s.state)).toEqual(["done", "skipped", "skipped", "skipped", "done"]);
    expect(steps[4]?.note).toBe("No change proposed");
  });

  it("stops at the phase where a run failed, was rejected or cancelled", () => {
    const failed = stepperModel("failed", [
      "created",
      "planning",
      "awaiting_approval",
      "executing",
      "failed",
    ]);
    expect(failed.map((s) => s.state)).toEqual(["done", "done", "failed", "skipped", "skipped"]);
    expect(stepperModel("rejected", ["planning", "awaiting_approval"])[1]).toMatchObject({
      state: "failed",
      note: "Rejected",
    });
    expect(stepperModel("budget_exhausted", ["created", "planning"])[0]?.state).toBe("failed");
  });
});
