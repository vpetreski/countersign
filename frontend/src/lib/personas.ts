import type { Persona, Role, RunView } from "./types";

export const DEFAULT_PERSONAS: Persona[] = [
  { key: "viewer", name: "Riley", role: "viewer", description: "Reads dashboards, runs and the audit log" },
  {
    key: "operator",
    name: "Sam",
    role: "operator",
    description: "Asks the agent for work, answers its questions, cancels runs",
  },
  { key: "approver", name: "Alex", role: "approver", description: "Countersigns change requests" },
  {
    key: "approver2",
    name: "Jordan",
    role: "approver",
    description: "Second approver, required for high-risk change requests",
  },
];

export const ROLE_ABILITIES: Record<Role, string[]> = {
  viewer: ["Read the estate, runs, traces and the audit log"],
  operator: ["Start runs", "Answer the agent's questions", "Advance and cancel runs"],
  approver: ["Countersign change requests", "Advance approved runs"],
};

export function roleLabel(role: Role): string {
  return role.charAt(0).toUpperCase() + role.slice(1);
}

export function canStartRun(persona: Persona | undefined): boolean {
  return persona?.role === "operator";
}

export function operatorOf(personas: Persona[]): Persona | undefined {
  return personas.find((p) => p.role === "operator");
}

/** Why the active persona cannot start a run, or null when it can. */
export function startBlockedReason(persona: Persona | undefined, personas: Persona[]): string | null {
  if (!persona) return "Loading personas…";
  if (canStartRun(persona)) return null;
  const op = operatorOf(personas);
  const who = op ? `Switch to ${op.name} (operator)` : "Switch to an operator";
  if (persona.role === "viewer")
    return `${persona.name} is a viewer and can only read. ${who} to start a run.`;
  return `${persona.name} is an approver: approvers countersign change requests but never request them. ${who} to start a run.`;
}

/** The persona key from a principal subject such as "ws_123:operator". */
export function personaKeyOf(subject: string | null | undefined): string | null {
  if (!subject) return null;
  const i = subject.lastIndexOf(":");
  return i >= 0 ? subject.slice(i + 1) : subject;
}

export interface CountersignState {
  can: boolean;
  reason: string | null;
}

/** Whether the persona may countersign this run now; the server's `run.can.decide` has the last word. */
export function countersignState(run: RunView, persona: Persona | undefined): CountersignState {
  if (!persona) return { can: false, reason: null };
  if (run.status !== "awaiting_approval" || !run.plan) {
    return { can: false, reason: "The run is not waiting for a countersign." };
  }
  if (personaKeyOf(run.created_by) === persona.key) {
    return {
      can: false,
      reason: `${persona.name} requested this change, so ${persona.name} cannot countersign it (separation of duties).`,
    };
  }
  if (persona.role !== "approver") {
    return { can: false, reason: `${persona.name} is a ${persona.role}; only approvers countersign.` };
  }
  if (run.approvals.some((d) => d.persona === persona.key && d.plan_hash === run.plan?.hash)) {
    return { can: false, reason: `${persona.name} already countersigned this change request.` };
  }
  if (!run.can.decide) return { can: false, reason: "The server does not allow this persona to decide." };
  return { can: true, reason: null };
}

/** The next approver who could countersign: not the requester and not already decided. */
export function nextApprover(run: RunView, personas: Persona[]): Persona | undefined {
  const requester = personaKeyOf(run.created_by);
  const decided = new Set(run.approvals.map((d) => d.persona));
  return personas.find((p) => p.role === "approver" && p.key !== requester && !decided.has(p.key));
}

export function personaName(key: string | null | undefined, personas: Persona[]): string {
  if (!key) return "—";
  return personas.find((p) => p.key === key)?.name ?? key;
}
