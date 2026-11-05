import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useRef } from "react";
import { request } from "./lib/api";
import { emptyTimeline, timelineReducer } from "./lib/timeline";
import type {
  AppConfig,
  AuditEntry,
  Dashboard,
  RunEvent,
  RunView,
  SourcesResponse,
  TraceResponse,
} from "./lib/types";
import { useSession } from "./session";

export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: () => request<AppConfig>("/api/config"),
    staleTime: 10 * 60_000,
  });
}

export function useDashboard() {
  const { api, workspace } = useSession();
  return useQuery({
    queryKey: ["dashboard", workspace.workspace_id],
    queryFn: () => api<Dashboard>("/api/dashboard"),
    staleTime: 30_000,
  });
}

export function useSources() {
  const { api, workspace } = useSession();
  return useQuery({
    queryKey: ["sources", workspace.workspace_id],
    queryFn: () => api<SourcesResponse>("/api/sources"),
    staleTime: 60_000,
  });
}

export function useAudit() {
  const { api, workspace } = useSession();
  return useQuery({
    queryKey: ["audit", workspace.workspace_id],
    queryFn: () => api<{ entries: AuditEntry[] }>("/api/audit?limit=200"),
  });
}

export function runKey(workspaceId: string, persona: string, runId: string | null) {
  return ["run", workspaceId, persona, runId] as const;
}

/** The run as the active persona sees it (its `can` block is persona-specific). Never moves backwards. */
export function useRun(runId: string | null, opts: { poll: boolean }) {
  const { api, workspace, persona } = useSession();
  const qc = useQueryClient();
  const key = runKey(workspace.workspace_id, persona.key, runId);
  return useQuery({
    queryKey: key,
    enabled: Boolean(runId),
    queryFn: async () => {
      const fresh = await api<RunView>(`/api/runs/${runId}`);
      const current = qc.getQueryData<RunView>(key);
      return current && current.id === fresh.id && current.version > fresh.version ? current : fresh;
    },
    refetchInterval: (q) => {
      const run = q.state.data;
      if (!opts.poll || !run || run.terminal) return false;
      return 2500;
    },
    retry: (count, err) =>
      !(err && "status" in err && (err as { status: number }).status === 404) && count < 2,
  });
}

export function useRunList() {
  const { api, workspace, persona } = useSession();
  return useQuery({
    queryKey: ["runs", workspace.workspace_id, persona.key],
    queryFn: () => api<{ runs: RunView[] }>("/api/runs?limit=20"),
    refetchInterval: (q) => (q.state.data?.runs.some((r) => !r.terminal) ? 5000 : false),
  });
}

export function useTrace(runId: string | null, version: number | undefined, enabled: boolean) {
  const { api, workspace } = useSession();
  return useQuery({
    queryKey: ["trace", workspace.workspace_id, runId, version],
    enabled: Boolean(runId) && enabled,
    queryFn: () => api<TraceResponse>(`/api/runs/${runId}/trace`),
    placeholderData: (prev) => prev,
  });
}

/**
 * The run's timeline, fetched incrementally with ?after=SEQ whenever the run's version changes. At most one
 * request is in flight; a change that arrives meanwhile triggers one more fetch afterwards.
 */
export function useRunEvents(runId: string | null, version: number | undefined): RunEvent[] {
  const { api } = useSession();
  const [state, dispatch] = useReducer(timelineReducer, emptyTimeline);
  const lastSeq = useRef(0);
  const inFlight = useRef(false);
  const dirty = useRef(false);
  const current = useRef(runId);
  current.current = runId;
  lastSeq.current = state.runId === runId ? state.lastSeq : 0;

  useEffect(() => {
    dispatch({ type: "reset", runId });
    lastSeq.current = 0;
  }, [runId]);

  const pull = useCallback(async () => {
    const id = current.current;
    if (!id) return;
    if (inFlight.current) {
      dirty.current = true;
      return;
    }
    inFlight.current = true;
    try {
      do {
        dirty.current = false;
        const res = await api<{ events: RunEvent[] }>(`/api/runs/${id}/events?after=${lastSeq.current}`);
        if (current.current !== id) return;
        dispatch({ type: "append", runId: id, events: res.events });
        const max = res.events.reduce((m, e) => Math.max(m, e.seq), lastSeq.current);
        lastSeq.current = max;
      } while (dirty.current && current.current === id);
    } catch {
      // The next version change retries; the run view itself reports errors.
    } finally {
      inFlight.current = false;
    }
  }, [api]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: refetch whenever the run's version moves
  useEffect(() => {
    if (runId) void pull();
  }, [runId, version, pull]);

  return state.runId === runId ? state.events : [];
}
