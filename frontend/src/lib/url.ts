import { useCallback, useSyncExternalStore } from "react";

// URL query state (?view=estate&run=run_123), shared by every hook and kept in step with back/forward.

const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("popstate", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("popstate", listener);
  };
}

export function getParam(name: string): string | null {
  return new URLSearchParams(window.location.search).get(name);
}

export function setParams(values: Record<string, string | null>, mode: "push" | "replace" = "push"): void {
  const params = new URLSearchParams(window.location.search);
  for (const [k, v] of Object.entries(values)) {
    if (v === null || v === "") params.delete(k);
    else params.set(k, v);
  }
  const qs = params.toString();
  const next = `${window.location.pathname}${qs ? `?${qs}` : ""}${window.location.hash}`;
  if (next === `${window.location.pathname}${window.location.search}${window.location.hash}`) return;
  if (mode === "push") window.history.pushState(null, "", next);
  else window.history.replaceState(null, "", next);
  for (const l of listeners) l();
}

export function useUrlParam(
  name: string,
): [string | null, (value: string | null, mode?: "push" | "replace") => void] {
  const value = useSyncExternalStore(
    subscribe,
    () => getParam(name),
    () => null,
  );
  const set = useCallback(
    (v: string | null, mode: "push" | "replace" = "push") => setParams({ [name]: v }, mode),
    [name],
  );
  return [value, set];
}

export function hrefWith(values: Record<string, string | null>): string {
  const params = new URLSearchParams(typeof window === "undefined" ? "" : window.location.search);
  for (const [k, v] of Object.entries(values)) {
    if (v === null) params.delete(k);
    else params.set(k, v);
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "?";
}
