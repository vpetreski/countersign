import {
  AlertTriangle,
  Check,
  ChevronRight,
  FileCheck2,
  Lock,
  PenLine,
  RotateCcw,
  ShieldCheck,
  ShieldX,
  Undo2,
  X,
} from "lucide-react";
import { Fragment, useEffect, useId, useState } from "react";
import { CitedSummary, CitedText, EvidenceChips } from "../../components/Citations";
import { Avatar } from "../../components/Header";
import { NotProposedTable } from "../../components/NotProposed";
import { Badge, Button, Card, cx, Mono, td, th } from "../../components/ui";
import { formatDateTime, formatMonthly, humanize, providerLabel, shortHash } from "../../lib/format";
import { countersignState, personaKeyOf } from "../../lib/personas";
import type { Persona, PlanAction, PolicyRule, RunView, Verdict } from "../../lib/types";
import { riskTone } from "./Timeline";

export function envTone(env: string): "danger" | "warn" | "info" | "neutral" {
  if (env === "prod") return "danger";
  if (env === "staging") return "warn";
  if (env === "dev") return "info";
  return "neutral";
}

export function ProviderBadge({ provider }: { provider: string }) {
  return (
    <Badge
      className={
        provider === "aws"
          ? "border-orange-500/30 bg-orange-500/10 text-orange-300"
          : "border-sky-500/30 bg-sky-500/10 text-sky-300"
      }
    >
      {providerLabel(provider)}
    </Badge>
  );
}

function operationLabel(a: Pick<PlanAction, "operation" | "current_sku" | "target_sku">) {
  return (
    <span className="flex flex-col">
      <span className="font-medium text-zinc-100">{humanize(a.operation)}</span>
      {a.operation === "resize_vm" && a.target_sku ? (
        <span className="font-mono text-[11px] text-zinc-400">
          {a.current_sku} → <span className="text-emerald-300">{a.target_sku}</span>
        </span>
      ) : (
        <span className="font-mono text-[11px] text-zinc-500">{a.current_sku}</span>
      )}
    </span>
  );
}

function PolicyChecks({ action, rules }: { action: PlanAction; rules: PolicyRule[] }) {
  return (
    <div className="grid gap-1.5 sm:grid-cols-2">
      {action.checks.map((c) => {
        const rule = rules.find((r) => r.id === c.rule);
        return (
          <div
            key={c.rule}
            className={cx(
              "flex items-start gap-2 rounded-lg border px-2.5 py-2",
              c.passed ? "border-zinc-800 bg-zinc-950/40" : "border-red-500/40 bg-red-500/10",
            )}
          >
            {c.passed ? (
              <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-emerald-400" aria-label="passed" />
            ) : (
              <ShieldX className="mt-0.5 size-3.5 shrink-0 text-red-400" aria-label="failed" />
            )}
            <div className="min-w-0 text-xs">
              <p>
                <Mono className="text-zinc-100">{c.rule}</Mono>{" "}
                <span className="text-zinc-300">{c.name}</span>
                <span className="text-zinc-500"> · {c.passed ? "passed" : "failed"}</span>
              </p>
              <p className="text-zinc-400">{c.detail}</p>
              {rule && <p className="mt-0.5 text-[11px] text-zinc-500">{rule.description}</p>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function DecisionToggle({
  value,
  onChange,
  label,
}: {
  value: Verdict;
  onChange: (v: Verdict) => void;
  label: string;
}) {
  return (
    <fieldset
      className="inline-flex rounded-lg border border-zinc-700 p-0.5"
      aria-label={`Decision for ${label}`}
    >
      <button
        type="button"
        aria-pressed={value === "approve"}
        onClick={() => onChange("approve")}
        className={cx(
          "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs transition",
          value === "approve" ? "bg-emerald-500/20 text-emerald-200" : "text-zinc-400 hover:text-zinc-200",
        )}
      >
        <Check className="size-3" aria-hidden /> Approve
      </button>
      <button
        type="button"
        aria-pressed={value === "reject"}
        onClick={() => onChange("reject")}
        className={cx(
          "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs transition",
          value === "reject" ? "bg-red-500/20 text-red-200" : "text-zinc-400 hover:text-zinc-200",
        )}
      >
        <X className="size-3" aria-hidden /> Reject
      </button>
    </fieldset>
  );
}

export function ChangeRequest({
  run,
  persona,
  personas,
  rules,
  onDecide,
  deciding,
}: {
  run: RunView;
  persona: Persona;
  personas: Persona[];
  rules: PolicyRule[];
  onDecide: (decisions: Record<string, Verdict>, note: string) => void;
  deciding: boolean;
}) {
  const plan = run.plan;
  const [decisions, setDecisions] = useState<Record<string, Verdict>>({});
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [note, setNote] = useState("");
  const noteId = useId();
  const hash = plan?.hash;
  // biome-ignore lint/correctness/useExhaustiveDependencies: a new plan hash starts a fresh review
  useEffect(() => {
    setDecisions({});
    setNote("");
  }, [hash]);
  if (!plan) return null;
  const sign = countersignState(run, persona);
  const verdict = (id: string): Verdict => decisions[id] ?? "approve";
  const approved = plan.actions.filter((a) => verdict(a.id) === "approve").length;
  const progress = Math.min(1, plan.approvals_received / Math.max(plan.approvals_required, 1));
  const signers = run.approvals.filter((d) => d.plan_hash === plan.hash);
  const executed = run.status !== "awaiting_approval";
  const notProposed = plan.not_proposed ?? run.not_proposed ?? [];

  return (
    <Card
      id="change-request"
      data-testid="change-request"
      title="Change request"
      icon={<FileCheck2 className="size-4" aria-hidden />}
      subtitle={
        <span className="flex flex-wrap items-center gap-x-2">
          <span>revision {plan.revision}</span>
          <span aria-hidden>·</span>
          <span>
            plan{" "}
            <Mono className="text-[11px] text-zinc-400" title={plan.hash}>
              {shortHash(plan.hash, 12)}
            </Mono>
          </span>
          <span aria-hidden>·</span>
          <span>policy v{plan.policy_version}</span>
        </span>
      }
      actions={
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={riskTone(plan.risk)}>risk {plan.risk}</Badge>
          <Badge tone="accent" className="text-sm">
            {formatMonthly(plan.total_monthly_savings)} estimated
          </Badge>
        </div>
      }
    >
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_260px]">
        <div className="min-w-0 space-y-4">
          <CitedSummary
            text={plan.summary}
            evidence={run.evidence}
            collapseItems
            hideExcluded={notProposed.length > 0}
          />
          {plan.caveats.length > 0 && (
            <div
              className="rounded-lg border border-amber-500/35 bg-amber-500/[0.07] px-3.5 py-2.5"
              data-testid="caveats"
            >
              <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-amber-300">
                <AlertTriangle className="size-3.5" aria-hidden /> Caveats
              </p>
              <ul className="mt-1.5 space-y-1 text-sm text-amber-100/90">
                {plan.caveats.map((c) => (
                  <li key={c} className="flex gap-2">
                    <span aria-hidden className="mt-2 size-1 shrink-0 rounded-full bg-amber-400" />
                    <CitedText text={c} evidence={run.evidence} />
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
        <aside className="rounded-lg border border-zinc-800 bg-zinc-950/40 p-3.5" aria-label="Approvals">
          <div className="flex items-baseline justify-between">
            <p className="text-xs text-zinc-400">Countersigns</p>
            <p className="text-sm font-semibold text-zinc-100" data-testid="approval-progress">
              {plan.approvals_received} of {plan.approvals_required} approvals
            </p>
          </div>
          <div
            className="mt-2 h-1.5 overflow-hidden rounded-full bg-zinc-800"
            role="progressbar"
            aria-label="Approvals received"
            aria-valuenow={plan.approvals_received}
            aria-valuemin={0}
            aria-valuemax={plan.approvals_required}
          >
            <div
              className="h-full rounded-full bg-emerald-400 transition-[width] duration-700"
              style={{ width: `${progress * 100}%` }}
            />
          </div>
          <p className="mt-2 text-[11px] text-zinc-500">
            {plan.risk === "high"
              ? "High risk: two distinct approvers must countersign."
              : `${plan.approvals_required} approver must countersign.`}{" "}
            The requester can never approve.
          </p>
          <ul className="mt-3 space-y-2">
            {signers.length === 0 && <li className="text-xs text-zinc-500">No countersigns yet.</li>}
            {signers.map((d) => {
              const p = personas.find((x) => x.key === d.persona);
              const rejected = Object.values(d.decisions).filter((v) => v === "reject").length;
              return (
                <li
                  key={d.approver}
                  className="flex items-center gap-2 text-xs animate-fade-in"
                  data-testid="signer"
                >
                  {p && <Avatar persona={p} size="sm" />}
                  <span className="min-w-0 flex-1">
                    <span className="font-medium text-zinc-100">{p?.name ?? d.persona}</span>
                    <span className="block text-zinc-500">{formatDateTime(d.decided_at)}</span>
                  </span>
                  <Badge tone={rejected ? "warn" : "accent"}>
                    {rejected ? `${rejected} rejected` : "approved"}
                  </Badge>
                </li>
              );
            })}
          </ul>
          <p className="mt-3 text-[11px] text-zinc-500">
            Requested by{" "}
            {personas.find((p) => p.key === personaKeyOf(run.created_by))?.name ?? "the operator"}
          </p>
        </aside>
      </div>

      <div className="scroll-thin -mx-5 mt-5 overflow-x-auto px-5">
        <table className="w-full min-w-[880px] border-separate border-spacing-0" data-testid="actions-table">
          <caption className="sr-only">Proposed actions</caption>
          <thead>
            <tr className="[&>th]:border-b [&>th]:border-zinc-800">
              <th className={th}>Resource</th>
              <th className={th}>Operation</th>
              <th className={cx(th, "text-right")}>Savings</th>
              <th className={th}>Risk</th>
              <th className={th}>Policy</th>
              <th className={th}>Evidence</th>
              <th className={th}>Approvals</th>
              {sign.can && <th className={th}>Your decision</th>}
            </tr>
          </thead>
          <tbody>
            {plan.actions.map((a) => {
              const passed = a.checks.filter((c) => c.passed).length;
              const isOpen = open[a.id] ?? false;
              return (
                <Fragment key={a.id}>
                  <tr
                    className="[&>td]:border-b [&>td]:border-zinc-800/70 hover:bg-zinc-800/20"
                    data-testid="action-row"
                  >
                    <td className={td}>
                      <div className="flex items-start gap-2">
                        <button
                          type="button"
                          onClick={() => setOpen((o) => ({ ...o, [a.id]: !isOpen }))}
                          aria-expanded={isOpen}
                          aria-label={`${isOpen ? "Hide" : "Show"} details for ${a.ref}`}
                          className="mt-0.5 rounded p-0.5 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200"
                        >
                          <ChevronRight
                            className={cx("size-4 transition-transform", isOpen && "rotate-90")}
                            aria-hidden
                          />
                        </button>
                        <div className="min-w-0">
                          <p className="flex items-center gap-1.5">
                            <Mono className="text-[11px] text-zinc-500">{a.id}</Mono>
                            <Mono className="text-zinc-100">{a.ref}</Mono>
                          </p>
                          {a.name !== a.ref && <p className="truncate text-xs text-zinc-400">{a.name}</p>}
                          <div className="mt-1 flex flex-wrap gap-1">
                            <ProviderBadge provider={a.provider} />
                            <Badge tone={envTone(a.environment)}>{a.environment}</Badge>
                            {a.team && <Badge>{a.team}</Badge>}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className={td}>
                      {operationLabel(a)}
                      <p className="mt-1 text-[11px] text-zinc-500">
                        {a.steps.length} step{a.steps.length === 1 ? "" : "s"}: {a.steps.join(" → ")}
                      </p>
                    </td>
                    <td className={cx(td, "text-right font-mono text-emerald-300 tabular-nums")}>
                      {formatMonthly(a.est_monthly_savings)}
                      {a.price_source && <p className="text-[11px] text-zinc-500">{a.price_source}</p>}
                    </td>
                    <td className={td}>
                      <div className="flex flex-col items-start gap-1">
                        <Badge tone={riskTone(a.risk)}>{a.risk}</Badge>
                        {a.reversible ? (
                          <Badge icon={<Undo2 className="size-3" aria-hidden />}>reversible</Badge>
                        ) : (
                          <Badge tone="warn" icon={<Lock className="size-3" aria-hidden />}>
                            irreversible
                          </Badge>
                        )}
                      </div>
                    </td>
                    <td className={td}>
                      <button
                        type="button"
                        onClick={() => setOpen((o) => ({ ...o, [a.id]: !isOpen }))}
                        className="inline-flex items-center gap-1 rounded-md border border-zinc-700/80 px-1.5 py-0.5 text-xs text-zinc-300 hover:border-zinc-600"
                        aria-label={`${passed} of ${a.checks.length} policy checks passed for ${a.ref}; show checks`}
                      >
                        <ShieldCheck className="size-3 text-emerald-400" aria-hidden />
                        {passed}/{a.checks.length} passed
                      </button>
                      {a.warnings.length > 0 && (
                        <p className="mt-1">
                          <Badge tone="warn" icon={<AlertTriangle className="size-3" aria-hidden />}>
                            {a.warnings.length} warning{a.warnings.length > 1 ? "s" : ""}
                          </Badge>
                        </p>
                      )}
                    </td>
                    <td className={td}>
                      <EvidenceChips ids={a.evidence_ids} evidence={run.evidence} />
                    </td>
                    <td className={td}>
                      <span className="text-xs text-zinc-300">
                        {a.approvals} approve
                        {a.rejections > 0 && <span className="text-red-300"> · {a.rejections} reject</span>}
                      </span>
                    </td>
                    {sign.can && (
                      <td className={td}>
                        <DecisionToggle
                          label={a.ref}
                          value={verdict(a.id)}
                          onChange={(v) => setDecisions((d) => ({ ...d, [a.id]: v }))}
                        />
                      </td>
                    )}
                  </tr>
                  {isOpen && (
                    <tr>
                      <td
                        colSpan={sign.can ? 8 : 7}
                        className="border-b border-zinc-800/70 bg-zinc-950/30 px-3 py-3"
                      >
                        <div className="space-y-3 pl-7">
                          <p className="text-sm text-zinc-300">
                            <span className="text-zinc-500">Rationale: </span>
                            <CitedText text={a.rationale} evidence={run.evidence} />
                          </p>
                          {a.warnings.length > 0 && (
                            <ul className="space-y-1 text-sm text-amber-200">
                              {a.warnings.map((w) => (
                                <li key={w} className="flex items-start gap-1.5">
                                  <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                                  {w}
                                </li>
                              ))}
                            </ul>
                          )}
                          <p className="text-xs text-zinc-500">
                            <Mono className="text-[11px] text-zinc-400">{a.resource_id}</Mono>
                            {a.owner && <> · owner {a.owner}</>}
                          </p>
                          <PolicyChecks action={a} rules={rules} />
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {notProposed.length > 0 && (
        <div className="mt-6">
          <NotProposedTable items={notProposed} evidence={run.evidence} />
        </div>
      )}

      {!executed && (
        <div className="mt-5 rounded-lg border border-zinc-800 bg-zinc-950/40 p-4">
          {sign.can ? (
            <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
              <div className="min-w-0 flex-1">
                <label htmlFor={noteId} className="text-xs text-zinc-400">
                  Note for the audit log (optional)
                </label>
                <input
                  id={noteId}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  maxLength={500}
                  placeholder="Reviewed the evidence; owners notified"
                  className="mt-1 h-9 w-full rounded-lg border border-zinc-700 bg-zinc-950/60 px-3 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-emerald-500/60 focus:outline-none"
                />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  icon={<RotateCcw className="size-3.5" aria-hidden />}
                  onClick={() =>
                    setDecisions(Object.fromEntries(plan.actions.map((a) => [a.id, "approve" as Verdict])))
                  }
                >
                  Approve all
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  icon={<X className="size-3.5" aria-hidden />}
                  onClick={() =>
                    setDecisions(Object.fromEntries(plan.actions.map((a) => [a.id, "reject" as Verdict])))
                  }
                >
                  Reject all
                </Button>
                <span className="text-xs text-zinc-400" aria-live="polite">
                  {approved} approve · {plan.actions.length - approved} reject
                </span>
                <Button
                  variant="primary"
                  size="lg"
                  loading={deciding}
                  icon={<PenLine className="size-4" aria-hidden />}
                  onClick={() =>
                    onDecide(Object.fromEntries(plan.actions.map((a) => [a.id, verdict(a.id)])), note.trim())
                  }
                  data-testid="countersign"
                >
                  Countersign as {persona.name}
                </Button>
              </div>
            </div>
          ) : (
            <p className="flex items-center gap-2 text-sm text-zinc-400" data-testid="countersign-blocked">
              <Lock className="size-4 shrink-0 text-zinc-500" aria-hidden />
              {sign.reason}
            </p>
          )}
        </div>
      )}
    </Card>
  );
}
