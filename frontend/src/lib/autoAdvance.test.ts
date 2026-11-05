import { describe, expect, it, vi } from "vitest";
import { ProblemError } from "./api";
import { AutoAdvancer, shouldAdvance } from "./autoAdvance";
import type { RunView } from "./types";

function run(status: string, canAdvance = true, version = 1): RunView {
  return {
    id: "run_1",
    status,
    terminal: ["completed", "failed", "rejected", "cancelled"].includes(status),
    version,
    can: { advance: canAdvance, answer: false, decide: false, cancel: false },
  } as RunView;
}

const flush = () => new Promise((r) => setTimeout(r, 0));
const instant = () => Promise.resolve();

describe("shouldAdvance", () => {
  it("advances only active statuses the persona may advance", () => {
    expect(shouldAdvance(run("planning"))).toBe(true);
    expect(shouldAdvance(run("executing"))).toBe(true);
    expect(shouldAdvance(run("awaiting_approval"))).toBe(false);
    expect(shouldAdvance(run("awaiting_input"))).toBe(false);
    expect(shouldAdvance(run("completed"))).toBe(false);
    expect(shouldAdvance(run("planning", false))).toBe(false);
    expect(shouldAdvance(undefined)).toBe(false);
  });
});

describe("AutoAdvancer", () => {
  it("ticks until the run waits for a human, one request at a time", async () => {
    const replies = [run("planning", true, 2), run("planning", true, 3), run("awaiting_approval", true, 4)];
    let inFlight = 0;
    let maxInFlight = 0;
    const advance = vi.fn(async () => {
      inFlight++;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await flush();
      inFlight--;
      return replies.shift() as RunView;
    });
    const seen: number[] = [];
    const idle = vi.fn();
    const ctl = new AutoAdvancer({
      advance,
      onRun: (r) => seen.push(r.version),
      onError: vi.fn(),
      onIdle: idle,
      sleep: instant,
    });
    expect(ctl.start(run("created"))).toBe(true);
    expect(ctl.start(run("created"))).toBe(false);
    await vi.waitFor(() => expect(idle).toHaveBeenCalled());
    expect(advance).toHaveBeenCalledTimes(3);
    expect(seen).toEqual([2, 3, 4]);
    expect(maxInFlight).toBe(1);
    expect(ctl.active).toBe(false);
  });

  it("waits the interval between ticks", async () => {
    const sleeps: number[] = [];
    const replies = [run("executing"), run("completed")];
    const ctl = new AutoAdvancer({
      advance: async () => replies.shift() as RunView,
      onRun: vi.fn(),
      onError: vi.fn(),
      intervalMs: 500,
      sleep: async (ms) => {
        sleeps.push(ms);
      },
    });
    const idle = new Promise<void>((resolve) => {
      ctl.start(run("executing"));
      const t = setInterval(() => {
        if (!ctl.active) {
          clearInterval(t);
          resolve();
        }
      }, 1);
    });
    await idle;
    expect(sleeps).toEqual([500]);
  });

  it("retries a 409 after a short delay and gives up on other errors", async () => {
    const conflict = new ProblemError(409, { title: "Conflict", detail: "leased", type: "x/conflict" });
    const replies: Array<RunView | Error> = [conflict, conflict, run("awaiting_approval")];
    const sleeps: number[] = [];
    const onError = vi.fn();
    const ctl = new AutoAdvancer({
      advance: async () => {
        const next = replies.shift();
        if (next instanceof Error) throw next;
        return next as RunView;
      },
      onRun: vi.fn(),
      onError,
      conflictDelayMs: 700,
      sleep: async (ms) => {
        sleeps.push(ms);
      },
    });
    ctl.start(run("planning"));
    await vi.waitFor(() => expect(ctl.active).toBe(false));
    expect(sleeps).toEqual([700, 700]);
    expect(onError).not.toHaveBeenCalled();

    const boom = new ProblemError(500, { title: "Internal Server Error" });
    const failing = new AutoAdvancer({
      advance: () => Promise.reject(boom),
      onRun: vi.fn(),
      onError,
      sleep: instant,
    });
    failing.start(run("planning"));
    await vi.waitFor(() => expect(onError).toHaveBeenCalledWith(boom));
  });

  it("drops a response that arrives after stop()", async () => {
    let release: (r: RunView) => void = () => {};
    const onRun = vi.fn();
    const ctl = new AutoAdvancer({
      advance: () => new Promise<RunView>((r) => (release = r)),
      onRun,
      onError: vi.fn(),
      sleep: instant,
    });
    ctl.start(run("planning"));
    ctl.stop();
    release(run("planning", true, 9));
    await flush();
    expect(onRun).not.toHaveBeenCalled();
    expect(ctl.active).toBe(false);
  });
});
