import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useIntentKey } from "../hooks";

describe("useIntentKey", () => {
  it("keeps one key per request until reset, so a retry replays instead of repeating", () => {
    const { result } = renderHook(() => useIntentKey());
    const body = { persona: "operator", body: { request: "Find idle VMs", faults: [] } };
    const first = result.current.take(body);
    expect(result.current.take({ ...body })).toBe(first);
    act(() => result.current.reset());
    const next = result.current.take(body);
    expect(next).not.toBe(first);
    expect(next).toMatch(/\S{8,}/);
  });

  it("gives a different request, or the same body from another persona, a new key", () => {
    const { result } = renderHook(() => useIntentKey());
    const body = { plan_hash: "abc", decisions: [{ action_id: "act-01", decision: "approve" }] };
    const alex = result.current.take({ persona: "approver", body });
    const jordan = result.current.take({ persona: "approver2", body });
    expect(jordan).not.toBe(alex);
    expect(result.current.take({ persona: "approver2", body: { ...body, plan_hash: "def" } })).not.toBe(
      jordan,
    );
  });
});
