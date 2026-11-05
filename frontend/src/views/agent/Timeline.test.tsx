import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { RunEvent } from "../../lib/types";
import { Timeline } from "./Timeline";

const ev = (seq: number, type: string, payload: Record<string, unknown> = {}): RunEvent => ({
  seq,
  at: "2026-11-01T00:00:00Z",
  type,
  payload,
});

const renderTimeline = (events: RunEvent[]) =>
  render(
    <Timeline
      events={events}
      actions={[]}
      faults={[]}
      pendingQuestionSeq={null}
      canAnswer={false}
      onAnswer={() => {}}
      answering={false}
      personaName={(key) => key}
    />,
  );

describe("Timeline policy rows", () => {
  it("names the submission check and the decision-time re-check apart", () => {
    renderTimeline([
      ev(1, "policy_evaluated", {
        stage: "submission",
        allowed: true,
        risk: "medium",
        approvals_required: 2,
      }),
      ev(2, "policy_evaluated", {
        stage: "decision",
        allowed: false,
        risk: "medium",
        approvals_required: 2,
        violations: [{ ref: "vol-0b17", rules: ["P-004"] }],
      }),
    ]);
    const rows = screen.getAllByTestId("event-policy");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Policy evaluated");
    expect(rows[0]).toHaveTextContent("allowed");
    expect(rows[1]).toHaveTextContent("Policy re-checked at decision");
    expect(rows[1]).toHaveTextContent("denied");
    expect(rows[1]).toHaveTextContent("vol-0b17 violates P-004; left out of the capability token");
  });

  it("keeps the plain title for events recorded before the stage field existed", () => {
    renderTimeline([
      ev(1, "policy_evaluated", { allowed: false, violations: [{ ref: "i-1", rules: ["P-001"] }] }),
    ]);
    const row = screen.getByTestId("event-policy");
    expect(row).toHaveTextContent("Policy evaluated");
    expect(row).toHaveTextContent("i-1 violates P-001");
    expect(row).not.toHaveTextContent("left out of the capability token");
  });
});
