import { describe, expect, it } from "vitest";
import {
  canStartRun,
  countersignState,
  DEFAULT_PERSONAS,
  nextApprover,
  personaKeyOf,
  startBlockedReason,
} from "./personas";
import type { Persona, RunView } from "./types";

const [riley, sam, alex, jordan] = DEFAULT_PERSONAS as [Persona, Persona, Persona, Persona];

function run(over: Partial<RunView> = {}): RunView {
  return {
    id: "run_1",
    status: "awaiting_approval",
    terminal: false,
    version: 3,
    request: "x",
    created_by: "ws_1:operator",
    model: "m",
    created_at: "",
    updated_at: "",
    faults: [],
    degraded: [],
    error: null,
    report: null,
    question: null,
    budgets: {} as RunView["budgets"],
    waiting_for: "approver",
    approvals: [],
    can: { advance: true, answer: false, decide: true, cancel: false },
    plan: {
      hash: "h1",
      summary: "",
      caveats: [],
      total_monthly_savings: 1,
      risk: "high",
      approvals_required: 2,
      approvals_received: 0,
      revision: 0,
      policy_version: 1,
      actions: [],
    },
    ...over,
  };
}

describe("persona permissions", () => {
  it("lets only the operator start a run, and says why otherwise", () => {
    expect(canStartRun(sam)).toBe(true);
    expect(canStartRun(riley)).toBe(false);
    expect(canStartRun(alex)).toBe(false);
    expect(startBlockedReason(sam, DEFAULT_PERSONAS)).toBeNull();
    expect(startBlockedReason(riley, DEFAULT_PERSONAS)).toMatch(/viewer.*Switch to Sam \(operator\)/);
    expect(startBlockedReason(alex, DEFAULT_PERSONAS)).toMatch(/approver/);
  });

  it("reads the persona from a principal subject", () => {
    expect(personaKeyOf("ws_abc:approver2")).toBe("approver2");
    expect(personaKeyOf(null)).toBeNull();
  });

  it("blocks the requester by separation of duties", () => {
    const s = countersignState(run(), sam);
    expect(s.can).toBe(false);
    expect(s.reason).toMatch(/separation of duties/);
  });

  it("blocks viewers and approvers who already decided", () => {
    expect(countersignState(run(), riley).can).toBe(false);
    const decided = run({
      approvals: [
        {
          approver: "ws_1:approver",
          persona: "approver",
          decided_at: "",
          plan_hash: "h1",
          decisions: {},
          note: null,
        },
      ],
    });
    expect(countersignState(decided, alex)).toEqual({
      can: false,
      reason: "Alex already countersigned this change request.",
    });
    expect(countersignState(decided, jordan).can).toBe(true);
  });

  it("only allows countersigning while the run awaits approval", () => {
    expect(countersignState(run({ status: "executing" }), alex).can).toBe(false);
    expect(
      countersignState(run({ can: { advance: true, answer: false, decide: false, cancel: false } }), alex)
        .can,
    ).toBe(false);
  });

  it("suggests the next approver who may still countersign", () => {
    expect(nextApprover(run(), DEFAULT_PERSONAS)?.name).toBe("Alex");
    const one = run({
      approvals: [
        {
          approver: "ws_1:approver",
          persona: "approver",
          decided_at: "",
          plan_hash: "h1",
          decisions: {},
          note: null,
        },
      ],
    });
    expect(nextApprover(one, DEFAULT_PERSONAS)?.name).toBe("Jordan");
  });
});
