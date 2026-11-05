import { History } from "lucide-react";
import { Card, cx, EmptyState, Meter, Skeleton } from "../../components/ui";
import { useRunList } from "../../hooks";
import { formatMonthly, formatTime } from "../../lib/format";
import { statusLabel, statusTone } from "../../lib/timeline";
import type { Budgets, RunView } from "../../lib/types";
import { useUrlParam } from "../../lib/url";

const DOT: Record<string, string> = {
  accent: "bg-emerald-400",
  warn: "bg-amber-400",
  danger: "bg-red-400",
  info: "bg-sky-400 animate-pulse",
  neutral: "bg-zinc-500",
};

export function RunList() {
  const runs = useRunList();
  const [current, setRun] = useUrlParam("run");
  return (
    <Card
      title="Runs in this workspace"
      icon={<History className="size-4" aria-hidden />}
      bodyClassName="px-2 py-2"
    >
      {runs.isPending ? (
        <div className="space-y-2 p-3">
          <Skeleton className="h-8" />
          <Skeleton className="h-8" />
        </div>
      ) : (runs.data?.runs.length ?? 0) === 0 ? (
        <EmptyState title="No runs yet" className="m-2 py-6">
          Start one with a preset; every run stays here so you can reopen it.
        </EmptyState>
      ) : (
        <ul className="scroll-thin max-h-80 space-y-0.5 overflow-y-auto" data-testid="run-list">
          {runs.data?.runs.map((r: RunView) => (
            <li key={r.id}>
              <button
                type="button"
                onClick={() => setRun(r.id)}
                aria-current={r.id === current ? "true" : undefined}
                className={cx(
                  "flex w-full items-start gap-2.5 rounded-lg px-3 py-2 text-left transition",
                  r.id === current ? "bg-zinc-800/80" : "hover:bg-zinc-800/40",
                )}
              >
                <span
                  aria-hidden
                  className={cx("mt-1.5 size-2 shrink-0 rounded-full", DOT[statusTone(r.status)])}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-zinc-200">{r.request}</span>
                  <span className="mt-0.5 flex flex-wrap gap-x-2 text-[11px] text-zinc-500">
                    <span>{statusLabel(r.status)}</span>
                    <span>{formatTime(r.created_at)}</span>
                    {r.plan && (
                      <span className="text-emerald-400/80">
                        {formatMonthly(r.plan.total_monthly_savings)}
                      </span>
                    )}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function BudgetMeters({ budgets }: { budgets: Budgets }) {
  return (
    <div className="space-y-3.5" data-testid="budgets">
      <Meter label="Model turns" value={budgets.model_turns} max={budgets.max_model_turns} />
      <Meter label="Tool calls" value={budgets.tool_calls} max={budgets.max_tool_calls} />
      <Meter
        label={budgets.simulated_tokens ? "Input tokens (simulated)" : "Input tokens"}
        value={budgets.input_tokens}
        max={budgets.max_input_tokens}
        hint={
          budgets.simulated_tokens
            ? "The offline policy reports estimated token counts; nothing is billed."
            : `${budgets.output_tokens.toLocaleString("en-US")} output tokens`
        }
      />
      <Meter label="Plan revisions" value={budgets.plan_revisions} max={budgets.max_plan_revisions} />
      {budgets.model_retries > 0 && (
        <p className="text-xs text-amber-300">{budgets.model_retries} model call(s) retried with backoff</p>
      )}
    </div>
  );
}
