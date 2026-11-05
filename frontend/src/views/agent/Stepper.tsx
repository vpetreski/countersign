import { AlertTriangle, Check, CircleDashed, Minus, X } from "lucide-react";
import { cx } from "../../components/ui";
import type { StepperStep } from "../../lib/timeline";

const STATE_TEXT: Record<StepperStep["state"], string> = {
  done: "done",
  current: "in progress",
  pending: "not started",
  skipped: "skipped",
  failed: "stopped here",
  warning: "finished with failures",
};

export function Stepper({ steps }: { steps: StepperStep[] }) {
  return (
    <ol className="flex w-full flex-wrap items-center gap-y-3" aria-label="Run progress">
      {steps.map((s, i) => (
        <li
          key={s.key}
          className={cx("flex items-center", i < steps.length - 1 && "flex-1")}
          data-step={s.key}
          data-state={s.state}
        >
          <div className="flex items-center gap-2">
            <span
              className={cx(
                "flex size-6 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold transition-colors duration-300",
                s.state === "done" && "border-emerald-400/60 bg-emerald-500/15 text-emerald-300",
                s.state === "current" && "border-sky-400/70 bg-sky-500/15 text-sky-200",
                s.state === "pending" && "border-zinc-700 text-zinc-500",
                s.state === "skipped" && "border-zinc-800 text-zinc-600",
                s.state === "failed" && "border-red-400/60 bg-red-500/15 text-red-300",
                s.state === "warning" && "border-amber-400/60 bg-amber-500/15 text-amber-300",
              )}
              aria-hidden
            >
              {s.state === "done" ? (
                <Check className="size-3.5" />
              ) : s.state === "current" ? (
                <CircleDashed className="size-3.5 animate-spin [animation-duration:3s]" />
              ) : s.state === "failed" ? (
                <X className="size-3.5" />
              ) : s.state === "warning" ? (
                <AlertTriangle className="size-3" />
              ) : s.state === "skipped" ? (
                <Minus className="size-3" />
              ) : (
                i + 1
              )}
            </span>
            <span className="flex flex-col leading-tight">
              <span
                className={cx(
                  "whitespace-nowrap text-xs font-medium",
                  s.state === "pending" || s.state === "skipped" ? "text-zinc-500" : "text-zinc-100",
                )}
              >
                {s.label}
                <span className="sr-only">: {STATE_TEXT[s.state]}</span>
              </span>
              {s.note && (
                <span
                  className={cx(
                    "text-[11px]",
                    s.state === "failed"
                      ? "text-red-300"
                      : s.state === "warning"
                        ? "text-amber-300"
                        : "text-zinc-400",
                  )}
                >
                  {s.note}
                </span>
              )}
            </span>
          </div>
          {i < steps.length - 1 && (
            <span
              aria-hidden
              className={cx(
                "mx-3 h-px min-w-4 flex-1 transition-colors duration-500",
                s.state === "done" ? "bg-emerald-500/50" : "bg-zinc-800",
              )}
            />
          )}
        </li>
      ))}
    </ol>
  );
}
