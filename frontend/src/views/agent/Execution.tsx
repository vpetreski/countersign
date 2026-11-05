import { BadgeCheck, CheckCircle2, CircleDashed, Hammer, RefreshCw, Undo2, XCircle } from "lucide-react";
import { Badge, Card, Mono } from "../../components/ui";
import { formatMonthly, formatTime, humanize, shortHash } from "../../lib/format";
import { providerApi } from "../../lib/providerApi";
import type { OutcomeStatus, RunView, StepStatus } from "../../lib/types";
import { stepTone } from "./Timeline";

function StepIcon({ status }: { status: StepStatus }) {
  if (status === "done") return <CheckCircle2 className="size-3.5 text-emerald-400" aria-hidden />;
  if (status === "replayed") return <RefreshCw className="size-3.5 text-sky-400" aria-hidden />;
  if (status === "compensated") return <Undo2 className="size-3.5 text-amber-400" aria-hidden />;
  return <XCircle className="size-3.5 text-red-400" aria-hidden />;
}

function outcomeTone(status: OutcomeStatus): "accent" | "warn" | "danger" | "neutral" | "info" {
  if (status === "succeeded") return "accent";
  if (status === "pending") return "info";
  if (status === "compensated") return "warn";
  if (status === "failed") return "danger";
  return "neutral";
}

export function Execution({ run }: { run: RunView }) {
  const actions = run.plan?.actions ?? [];
  if (!actions.some((a) => a.outcome)) return null;
  const realized = run.realized_monthly_savings ?? 0;
  const verifiedCount = actions.filter((a) => a.outcome?.verified).length;
  const approvedCount = actions.filter((a) => a.outcome && a.outcome.status !== "rejected").length;
  return (
    <Card
      title="Execution"
      icon={<Hammer className="size-4" aria-hidden />}
      subtitle="Each action runs as a saga of idempotent provider calls, then a fresh read verifies the end state."
      data-testid="execution"
      actions={
        <div className="flex items-center gap-2">
          <Badge tone={verifiedCount === approvedCount && approvedCount > 0 ? "accent" : "neutral"}>
            {verifiedCount} of {approvedCount} verified
          </Badge>
          <span
            className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-sm font-semibold text-emerald-300"
            data-testid="realized-savings"
            data-value={realized}
          >
            {formatMonthly(realized)} realized
          </span>
        </div>
      }
    >
      <ul className="grid gap-3 md:grid-cols-2">
        {actions.map((a) => {
          const o = a.outcome;
          if (!o) return null;
          return (
            <li
              key={a.id}
              className="rounded-lg border border-zinc-800 bg-zinc-950/40 p-3 animate-fade-in"
              data-testid="outcome"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Mono className="text-[11px] text-zinc-500">{a.id}</Mono>
                <Mono className="text-zinc-100">{a.ref}</Mono>
                <span className="text-xs text-zinc-400">{humanize(a.operation)}</span>
                <span className="ml-auto flex items-center gap-1.5">
                  <Badge tone={outcomeTone(o.status)}>
                    {o.status === "pending" && <CircleDashed className="size-3 animate-spin" aria-hidden />}
                    {o.status}
                  </Badge>
                  {o.verified === true && (
                    <Badge tone="accent" icon={<BadgeCheck className="size-3" aria-hidden />}>
                      verified
                    </Badge>
                  )}
                  {o.verified === false && <Badge tone="danger">not verified</Badge>}
                </span>
              </div>
              {o.steps.length > 0 && (
                <ol className="mt-2 space-y-1.5">
                  {o.steps.map((s) => (
                    <li key={`${s.step}-${s.idempotency_key}`} className="flex items-start gap-2 text-xs">
                      <span className="mt-0.5">
                        <StepIcon status={s.status} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-center gap-1.5">
                          <Mono className="text-zinc-200">{providerApi(a.provider, s.step)}</Mono>
                          <Badge tone={stepTone(s.status)}>{s.status}</Badge>
                          {s.attempts > 1 && (
                            <Badge tone="warn" icon={<RefreshCw className="size-3" aria-hidden />}>
                              retried · {s.attempts} attempts
                            </Badge>
                          )}
                        </span>
                        <span className="mt-0.5 block text-zinc-500">
                          {s.detail} · key{" "}
                          <Mono className="text-[11px] text-zinc-400" title={s.idempotency_key}>
                            {shortHash(s.idempotency_key, 10)}
                          </Mono>{" "}
                          · {formatTime(s.at)}
                        </span>
                      </span>
                    </li>
                  ))}
                </ol>
              )}
              <div className="mt-2 flex items-center justify-between border-t border-zinc-800/70 pt-2 text-xs">
                <span className="text-zinc-500">
                  {o.detail && o.detail !== o.steps[o.steps.length - 1]?.detail
                    ? o.detail
                    : o.status === "pending"
                      ? "queued"
                      : humanize(o.status)}
                </span>
                <span className="font-mono text-emerald-300">
                  {o.realized_monthly_savings > 0 ? formatMonthly(o.realized_monthly_savings) : "—"}
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
