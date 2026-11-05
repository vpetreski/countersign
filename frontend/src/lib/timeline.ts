import type { RunEvent, RunStatus } from "./types";

// ---------------------------------------------------------------- incremental event log

export interface TimelineState {
  runId: string | null;
  lastSeq: number;
  events: RunEvent[];
}

export const emptyTimeline: TimelineState = { runId: null, lastSeq: 0, events: [] };

export type TimelineAction =
  | { type: "reset"; runId: string | null }
  | { type: "append"; runId: string; events: RunEvent[] };

/** Merge a page from GET /events?after=SEQ: deduplicated by seq, ordered, and never mixed across runs. */
export function timelineReducer(state: TimelineState, action: TimelineAction): TimelineState {
  if (action.type === "reset") return { runId: action.runId, lastSeq: 0, events: [] };
  if (action.runId !== state.runId) return state;
  const fresh = action.events.filter((e) => e.seq > state.lastSeq && e.type !== "span");
  if (fresh.length === 0) return state;
  const bySeq = new Map<number, RunEvent>();
  for (const e of state.events) bySeq.set(e.seq, e);
  for (const e of fresh) bySeq.set(e.seq, e);
  const events = [...bySeq.values()].sort((a, b) => a.seq - b.seq);
  return { runId: state.runId, lastSeq: events[events.length - 1]?.seq ?? state.lastSeq, events };
}

// ---------------------------------------------------------------- display items

export interface TimelineItem {
  key: string;
  seq: number;
  at: string;
  type: string;
  payload: Record<string, unknown>;
  /** Later events folded into this one (a tool_error joins the tool_call with the same id). */
  related: RunEvent[];
}

export function buildTimeline(events: RunEvent[]): TimelineItem[] {
  const items: TimelineItem[] = [];
  const toolCalls = new Map<string, TimelineItem>();
  for (const e of events) {
    if (e.type === "span") continue;
    const id = typeof e.payload.id === "string" ? e.payload.id : null;
    if (e.type === "tool_error" && id && toolCalls.has(id)) {
      toolCalls.get(id)?.related.push(e);
      continue;
    }
    const item: TimelineItem = {
      key: `${e.seq}`,
      seq: e.seq,
      at: e.at,
      type: e.type,
      payload: e.payload,
      related: [],
    };
    if (e.type === "tool_call" && id) toolCalls.set(id, item);
    items.push(item);
  }
  return items;
}

/** The seq of the question the operator still has to answer, if the run is waiting for one. */
export function pendingQuestionSeq(events: RunEvent[], status: RunStatus | undefined): number | null {
  if (status !== "awaiting_input") return null;
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e?.type === "answer") return null;
    if (e?.type === "question") return e.seq;
  }
  return null;
}

// ---------------------------------------------------------------- status stepper

export type StepState = "done" | "current" | "pending" | "skipped" | "failed" | "warning";

export interface StepperStep {
  key: string;
  label: string;
  state: StepState;
  note?: string;
}

const PHASES = [
  { key: "planning", label: "Planning" },
  { key: "awaiting_approval", label: "Awaiting approval" },
  { key: "executing", label: "Executing" },
  { key: "verifying", label: "Verifying" },
  { key: "completed", label: "Completed" },
] as const;

const TERMINAL_LABEL: Partial<Record<RunStatus, string>> = {
  failed: "Failed",
  rejected: "Rejected",
  cancelled: "Cancelled",
  budget_exhausted: "Budget exhausted",
  expired: "Expired",
};

export function phaseIndex(status: string): number {
  switch (status) {
    case "created":
    case "planning":
    case "awaiting_input":
      return 0;
    case "awaiting_approval":
      return 1;
    case "executing":
      return 2;
    case "verifying":
      return 3;
    case "completed":
    case "completed_with_failures":
      return 4;
    default:
      return -1;
  }
}

/** Every status the run has been in, in order, from its status events. */
export function visitedStatuses(events: RunEvent[]): string[] {
  const out: string[] = [];
  for (const e of events) {
    if (e.type !== "status") continue;
    for (const k of ["from", "to"] as const) {
      const v = e.payload[k];
      if (typeof v === "string" && out[out.length - 1] !== v) out.push(v);
    }
  }
  return out;
}

export function stepperModel(status: RunStatus, visited: string[]): StepperStep[] {
  const steps: StepperStep[] = PHASES.map((p) => ({ key: p.key, label: p.label, state: "pending" }));
  const set = (i: number, state: StepState, note?: string) => {
    const s = steps[i];
    if (s) {
      s.state = state;
      if (note) s.note = note;
    }
  };
  if (status === "completed" || status === "completed_with_failures") {
    const hadPlan = visited.includes("awaiting_approval") || visited.includes("executing");
    if (!hadPlan) {
      set(0, "done");
      for (const i of [1, 2, 3]) set(i, "skipped");
      set(4, "done", "No change proposed");
      return steps;
    }
    for (const i of [0, 1, 2, 3]) set(i, "done");
    if (status === "completed") set(4, "done");
    else {
      set(4, "warning", "With failures");
      const last = steps[4];
      if (last) last.label = "Completed with failures";
    }
    return steps;
  }
  const terminal = TERMINAL_LABEL[status];
  if (terminal) {
    const lastPhase = [...visited].reverse().find((v) => phaseIndex(v) >= 0 && phaseIndex(v) < 4);
    const at = status === "rejected" ? 1 : Math.max(0, lastPhase ? phaseIndex(lastPhase) : 0);
    for (let i = 0; i < at; i++) set(i, "done");
    set(at, "failed", terminal);
    for (let i = at + 1; i < steps.length; i++) set(i, "skipped");
    return steps;
  }
  const at = Math.max(0, phaseIndex(status));
  for (let i = 0; i < at; i++) set(i, "done");
  set(
    at,
    "current",
    status === "awaiting_input" ? "Needs an answer" : status === "created" ? "Starting" : undefined,
  );
  return steps;
}

export function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    created: "Created",
    planning: "Planning",
    awaiting_input: "Awaiting input",
    awaiting_approval: "Awaiting approval",
    executing: "Executing",
    verifying: "Verifying",
    completed: "Completed",
    completed_with_failures: "Completed with failures",
    failed: "Failed",
    rejected: "Rejected",
    cancelled: "Cancelled",
    budget_exhausted: "Budget exhausted",
    expired: "Expired",
  };
  return labels[status] ?? status;
}

export type StatusTone = "neutral" | "info" | "accent" | "warn" | "danger";

export function statusTone(status: string): StatusTone {
  if (status === "completed") return "accent";
  if (status === "completed_with_failures" || status === "awaiting_approval" || status === "awaiting_input")
    return "warn";
  if (["failed", "rejected", "cancelled", "budget_exhausted", "expired"].includes(status)) return "danger";
  if (["planning", "executing", "verifying", "created"].includes(status)) return "info";
  return "neutral";
}
