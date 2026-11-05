import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useToast } from "./components/toast";
import { isProblem, type RequestOptions, request } from "./lib/api";
import { DEFAULT_PERSONAS } from "./lib/personas";
import type { Persona, Workspace } from "./lib/types";
import { setParams } from "./lib/url";

// One demo workspace per browser tab: created on first load, kept in sessionStorage, replaced transparently
// when the server says it expired. Every estate and run call carries the active persona's token.

export const WORKSPACE_KEY = "countersign.workspace";
export const PERSONA_KEY = "countersign.persona";
const DEFAULT_PERSONA = "operator";

export type Api = <T>(path: string, opts?: RequestOptions & { as?: string }) => Promise<T>;

interface SessionValue {
  workspace: Workspace;
  personas: Persona[];
  persona: Persona;
  setPersona: (key: string) => void;
  api: Api;
}

const SessionContext = createContext<SessionValue | null>(null);

function loadStored(): Workspace | null {
  try {
    const raw = sessionStorage.getItem(WORKSPACE_KEY);
    if (!raw) return null;
    const ws = JSON.parse(raw) as Workspace;
    if (!ws.workspace_id || !ws.tokens || new Date(ws.expires_at).getTime() < Date.now() + 60_000)
      return null;
    return ws;
  } catch {
    return null;
  }
}

function isSessionLost(err: unknown): boolean {
  return isProblem(err, 401) && (err.code === "workspace_expired" || err.code === "unauthorized");
}

export function SessionProvider({ children, fallback }: { children: ReactNode; fallback: ReactNode }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [workspace, setWorkspace] = useState<Workspace | null>(() => loadStored());
  const [error, setError] = useState<unknown>(null);
  const [personaKey, setPersonaKey] = useState<string>(
    () => sessionStorage.getItem(PERSONA_KEY) ?? DEFAULT_PERSONA,
  );
  const wsRef = useRef<Workspace | null>(workspace);
  const personaRef = useRef(personaKey);
  const creating = useRef<Promise<Workspace> | null>(null);
  personaRef.current = personaKey;

  const create = useCallback(async (): Promise<Workspace> => {
    if (!creating.current) {
      creating.current = request<Workspace>("/api/workspaces", { method: "POST" })
        .then((ws) => {
          sessionStorage.setItem(WORKSPACE_KEY, JSON.stringify(ws));
          wsRef.current = ws;
          setWorkspace(ws);
          setError(null);
          return ws;
        })
        .finally(() => {
          creating.current = null;
        });
    }
    return creating.current;
  }, []);

  useEffect(() => {
    if (!wsRef.current) create().catch(setError);
  }, [create]);

  const api = useCallback<Api>(
    async <T,>(path: string, opts: RequestOptions & { as?: string } = {}) => {
      const { as, ...rest } = opts;
      const call = (ws: Workspace) =>
        request<T>(path, { ...rest, token: ws.tokens[as ?? personaRef.current] });
      const ws = wsRef.current ?? (await create());
      try {
        return await call(ws);
      } catch (err) {
        if (!isSessionLost(err)) throw err;
        const fresh = await create();
        setParams({ run: null }, "replace");
        queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== "config" });
        toast.push({
          tone: "info",
          title: "New demo workspace",
          detail: "The previous workspace expired, so a fresh one was created with the same estate.",
        });
        return call(fresh);
      }
    },
    [create, queryClient, toast],
  );

  const personas = workspace?.personas?.length ? workspace.personas : DEFAULT_PERSONAS;
  const persona = personas.find((p) => p.key === personaKey) ?? personas[1] ?? DEFAULT_PERSONAS[1];

  const setPersona = useCallback((key: string) => {
    sessionStorage.setItem(PERSONA_KEY, key);
    personaRef.current = key;
    setPersonaKey(key);
  }, []);

  const value = useMemo(
    () => (workspace && persona ? { workspace, personas, persona, setPersona, api } : null),
    [workspace, personas, persona, setPersona, api],
  );

  if (!value) {
    return (
      <>
        {error ? (
          <div
            role="alert"
            className="mx-auto mt-24 max-w-md rounded-xl border border-red-500/40 bg-red-500/10 p-5 text-sm"
          >
            <p className="font-semibold text-red-200">Could not create a demo workspace</p>
            <p className="mt-1 text-red-200/80">
              {isProblem(error) ? `${error.title}: ${error.detail}` : "The API is unreachable."}
            </p>
            <button
              type="button"
              className="mt-3 rounded-lg border border-red-400/40 px-3 py-1.5 text-red-100 hover:bg-red-500/20"
              onClick={() => {
                setError(null);
                create().catch(setError);
              }}
            >
              Try again
            </button>
          </div>
        ) : (
          fallback
        )}
      </>
    );
  }
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession outside SessionProvider");
  return ctx;
}
