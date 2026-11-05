import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { createContext, type ReactNode, useCallback, useContext, useMemo, useRef, useState } from "react";
import { ProblemError } from "../lib/api";
import { cx } from "./ui";

export interface ToastInput {
  title: string;
  detail?: string;
  tone?: "error" | "info" | "success";
  errors?: Array<{ field: string; problem: string }>;
}

interface ToastItem extends ToastInput {
  id: number;
}

interface ToastApi {
  push: (t: ToastInput) => void;
  problem: (err: unknown, fallbackTitle?: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const next = useRef(1);
  const dismiss = useCallback((id: number) => setItems((xs) => xs.filter((x) => x.id !== id)), []);
  const push = useCallback(
    (t: ToastInput) => {
      const id = next.current++;
      setItems((xs) => [...xs.slice(-3), { ...t, id }]);
      window.setTimeout(() => dismiss(id), t.tone === "error" ? 9000 : 5000);
    },
    [dismiss],
  );
  const problem = useCallback(
    (err: unknown, fallbackTitle = "Something went wrong") => {
      if (err instanceof ProblemError) {
        push({ tone: "error", title: err.title, detail: err.detail, errors: err.errors });
      } else if (err instanceof Error) {
        push({ tone: "error", title: fallbackTitle, detail: err.message });
      } else {
        push({ tone: "error", title: fallbackTitle });
      }
    },
    [push],
  );
  const api = useMemo(() => ({ push, problem }), [push, problem]);
  return (
    <ToastContext.Provider value={api}>
      {children}
      <section
        aria-label="Notifications"
        aria-live="polite"
        className="pointer-events-none fixed inset-x-0 bottom-0 z-[100] flex flex-col items-center gap-2 p-4 sm:items-end"
      >
        {items.map((t) => (
          <div
            key={t.id}
            role={t.tone === "error" ? "alert" : "status"}
            className={cx(
              "pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-xl border bg-zinc-900/95 px-4 py-3 shadow-2xl shadow-black/50 backdrop-blur animate-fade-in",
              t.tone === "error"
                ? "border-red-500/40"
                : t.tone === "success"
                  ? "border-emerald-500/40"
                  : "border-zinc-700",
            )}
          >
            {t.tone === "error" ? (
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-red-400" aria-hidden />
            ) : t.tone === "success" ? (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-400" aria-hidden />
            ) : (
              <Info className="mt-0.5 size-4 shrink-0 text-sky-400" aria-hidden />
            )}
            <div className="min-w-0 flex-1 text-sm">
              <p className="font-medium text-zinc-100">{t.title}</p>
              {t.detail && <p className="mt-0.5 text-zinc-400">{t.detail}</p>}
              {t.errors && t.errors.length > 0 && (
                <ul className="mt-1 list-disc pl-4 text-xs text-zinc-400">
                  {t.errors.map((e) => (
                    <li key={`${e.field}:${e.problem}`}>
                      <span className="font-mono text-zinc-300">{e.field || "body"}</span>: {e.problem}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              className="rounded p-0.5 text-zinc-500 hover:text-zinc-200"
              aria-label="Dismiss notification"
            >
              <X className="size-4" aria-hidden />
            </button>
          </div>
        ))}
      </section>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast outside ToastProvider");
  return ctx;
}
