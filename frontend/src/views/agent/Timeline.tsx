import {
  AlertTriangle,
  ArrowRight,
  Bot,
  CheckCircle2,
  CircleDot,
  FileCheck2,
  FlaskConical,
  KeyRound,
  MessageCircleQuestion,
  MessageSquareReply,
  PenLine,
  RefreshCw,
  ScrollText,
  ShieldAlert,
  ShieldCheck,
  Terminal,
  Wrench,
  XCircle,
} from "lucide-react";
import { type ReactNode, useState } from "react";
import { CitedText, EvidenceChips } from "../../components/Citations";
import { Badge, Button, cx, Mono } from "../../components/ui";
import { formatDuration, formatInt, formatMonthly, formatTime, humanize, shortHash } from "../../lib/format";
import { personaKeyOf } from "../../lib/personas";
import { providerApi } from "../../lib/providerApi";
import { buildTimeline, statusLabel, type TimelineItem } from "../../lib/timeline";
import type { Evidence, FaultInfo, PlanAction, RunEvent } from "../../lib/types";

type P = Record<string, unknown>;
const str = (v: unknown): string =>
  typeof v === "string" ? v : v === null || v === undefined ? "" : String(v);
const num = (v: unknown): number => (typeof v === "number" ? v : Number(v) || 0);
const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

interface Ctx {
  evidence?: Record<string, Evidence>;
  actions: Map<string, PlanAction>;
  faults: FaultInfo[];
  pendingQuestionSeq: number | null;
  canAnswer: boolean;
  onAnswer: (answer: string) => void;
  answering: boolean;
  personaName: (key: string) => string;
}

function Row({
  icon,
  tone = "neutral",
  title,
  at,
  children,
  testId,
}: {
  icon: ReactNode;
  tone?: "neutral" | "accent" | "warn" | "danger" | "info" | "violet";
  title: ReactNode;
  at: string;
  children?: ReactNode;
  testId?: string;
}) {
  const ring = {
    neutral: "border-zinc-700 bg-zinc-900 text-zinc-400",
    accent: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
    warn: "border-amber-500/40 bg-amber-500/10 text-amber-300",
    danger: "border-red-500/40 bg-red-500/10 text-red-300",
    info: "border-sky-500/40 bg-sky-500/10 text-sky-300",
    violet: "border-violet-500/40 bg-violet-500/10 text-violet-300",
  }[tone];
  return (
    <li className="relative flex gap-3 pb-4 animate-fade-in" data-testid={testId}>
      <span aria-hidden className="absolute top-7 bottom-0 left-[13px] w-px bg-zinc-800" />
      <span
        className={cx(
          "relative z-10 flex size-7 shrink-0 items-center justify-center rounded-full border",
          ring,
        )}
      >
        {icon}
      </span>
      <div className="min-w-0 flex-1 pt-0.5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 text-sm text-zinc-200">{title}</div>
          <time className="shrink-0 font-mono text-[11px] text-zinc-500" dateTime={at}>
            {formatTime(at)}
          </time>
        </div>
        {children && <div className="mt-1.5 min-w-0 text-sm text-zinc-400">{children}</div>}
      </div>
    </li>
  );
}

function Json({ value }: { value: unknown }) {
  return (
    <pre className="scroll-thin mt-1 max-h-72 overflow-auto rounded-lg border border-zinc-800 bg-zinc-950/70 p-3 font-mono text-[11px] leading-relaxed text-zinc-300">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function inputSummary(input: unknown): string {
  if (!input || typeof input !== "object") return "";
  const entries = Object.entries(input as P);
  if (entries.length === 0) return "()";
  return `(${entries
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ")
    .slice(0, 140)})`;
}

function AnswerForm({
  options,
  onAnswer,
  busy,
}: {
  options: string[];
  onAnswer: (a: string) => void;
  busy: boolean;
}) {
  const [text, setText] = useState("");
  return (
    <div className="mt-2 rounded-lg border border-sky-500/30 bg-sky-500/[0.06] p-3" data-testid="answer-form">
      <p className="text-xs text-sky-200">Answer as the operator:</p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {options.map((o) => (
          <Button key={o} size="sm" variant="secondary" disabled={busy} onClick={() => onAnswer(o)}>
            {o}
          </Button>
        ))}
      </div>
      <form
        className="mt-2 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) onAnswer(text.trim());
        }}
      >
        <label className="sr-only" htmlFor="answer-text">
          Your answer
        </label>
        <input
          id="answer-text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={500}
          placeholder="Or type an answer"
          className="h-8 min-w-0 flex-1 rounded-lg border border-zinc-700 bg-zinc-950/60 px-2.5 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-sky-500/60 focus:outline-none"
        />
        <Button size="sm" type="submit" variant="accent-soft" disabled={!text.trim()} loading={busy}>
          Send
        </Button>
      </form>
    </div>
  );
}

function renderItem(item: TimelineItem, ctx: Ctx): ReactNode {
  const p = item.payload;
  const at = item.at;
  switch (item.type) {
    case "created": {
      const redacted = arr<unknown>(p.redacted);
      return (
        <Row key={item.key} at={at} icon={<PenLine className="size-3.5" />} title="Run created">
          <p className="rounded-lg border border-zinc-800 bg-zinc-950/40 px-3 py-2 text-zinc-300">
            “{str(p.request)}”
          </p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            <Badge mono>{str(p.model)}</Badge>
            {arr<string>(p.faults).map((f) => (
              <Badge key={f} tone="violet" icon={<FlaskConical className="size-3" aria-hidden />}>
                {ctx.faults.find((x) => x.name === f)?.title ?? f}
              </Badge>
            ))}
            {redacted.length > 0 && (
              <Badge tone="warn">{redacted.length} secret value(s) redacted before the model</Badge>
            )}
          </div>
        </Row>
      );
    }
    case "status":
      return (
        <Row
          key={item.key}
          at={at}
          tone="info"
          icon={<CircleDot className="size-3.5" />}
          title={
            <span className="flex flex-wrap items-center gap-1.5">
              <span className="text-zinc-400">{statusLabel(str(p.from))}</span>
              <ArrowRight className="size-3 text-zinc-500" aria-label="to" />
              <span className="font-medium text-zinc-100">{statusLabel(str(p.to))}</span>
            </span>
          }
        >
          {str(p.reason) && <span className="text-xs">{str(p.reason)}</span>}
        </Row>
      );
    case "model_turn": {
      const usage = (p.usage ?? {}) as P;
      const calls = arr<{ id: string; name: string }>(p.tool_calls);
      return (
        <Row
          key={item.key}
          at={at}
          tone="info"
          icon={<Bot className="size-3.5" />}
          title={
            <span className="flex flex-wrap items-center gap-2">
              <span className="font-medium">Model turn {num(p.turn)}</span>
              <Badge mono>{str(p.stop_reason)}</Badge>
            </span>
          }
          testId="event-model-turn"
        >
          {str(p.text) && (
            <p className="text-zinc-300">
              <CitedText text={str(p.text)} evidence={ctx.evidence} />
            </p>
          )}
          {calls.length > 0 && (
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              <span className="text-xs text-zinc-500">calls</span>
              {calls.map((c) => (
                <Badge key={c.id} mono tone="sky" icon={<Wrench className="size-3" aria-hidden />}>
                  {c.name}
                </Badge>
              ))}
            </div>
          )}
          <p className="mt-1.5 text-[11px] text-zinc-500">
            {formatInt(num(usage.input_tokens))} input · {formatInt(num(usage.output_tokens))} output tokens
            {p.simulated === true && (
              <span className="text-zinc-400">
                {" "}
                · simulated tokens: the offline policy estimates counts, nothing is billed
              </span>
            )}
          </p>
        </Row>
      );
    }
    case "tool_call": {
      const ok = p.ok === true;
      const err = item.related.find((r) => r.type === "tool_error");
      return (
        <Row
          key={item.key}
          at={at}
          tone={ok ? "neutral" : "danger"}
          icon={<Terminal className="size-3.5" />}
          title={
            <span className="flex flex-wrap items-center gap-2">
              <Mono className="text-zinc-100">{str(p.name)}</Mono>
              <Mono className="truncate text-zinc-500">{inputSummary(p.input)}</Mono>
              {ok ? (
                <Badge tone="accent" icon={<CheckCircle2 className="size-3" aria-hidden />}>
                  ok
                </Badge>
              ) : (
                <Badge tone="danger" icon={<XCircle className="size-3" aria-hidden />}>
                  {str(p.error_kind) || "error"}
                </Badge>
              )}
              <span className="font-mono text-[11px] text-zinc-500">
                {formatDuration(num(p.duration_ms))}
              </span>
            </span>
          }
          testId="event-tool-call"
        >
          <div className="flex flex-wrap items-center gap-1.5">
            {arr<string>(p.evidence_ids).length > 0 && (
              <>
                <span className="text-xs text-zinc-500">evidence</span>
                <EvidenceChips ids={arr<string>(p.evidence_ids)} evidence={ctx.evidence} />
              </>
            )}
          </div>
          {err && (
            <p className="mt-1 text-xs text-red-300">
              {str(err.payload.kind)}: {str(err.payload.error) || "the tool returned an error"}
            </p>
          )}
          <details className="group mt-1">
            <summary className="inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-300">
              <span className="group-open:hidden">Show</span>
              <span className="hidden group-open:inline">Hide</span> input and output preview
            </summary>
            <Json value={{ input: p.input, output: p.output }} />
          </details>
        </Row>
      );
    }
    case "tool_error":
      return (
        <Row
          key={item.key}
          at={at}
          tone="danger"
          icon={<XCircle className="size-3.5" />}
          title={`Tool error · ${str(p.name)}`}
        >
          <span className="text-xs text-red-300">
            {str(p.kind)} {str(p.error)}
          </span>
        </Row>
      );
    case "degraded":
      return (
        <li key={item.key} className="pb-4 animate-fade-in" data-testid="event-degraded">
          <div className="flex items-start gap-2.5 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2.5 text-sm text-amber-100">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-300" aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="font-medium">Degraded</p>
              <p className="text-amber-100/80">{str(p.reason)}</p>
            </div>
            <time className="font-mono text-[11px] text-amber-200/60" dateTime={at}>
              {formatTime(at)}
            </time>
          </div>
        </li>
      );
    case "model_retry":
      return (
        <Row
          key={item.key}
          at={at}
          tone="warn"
          icon={<RefreshCw className="size-3.5" />}
          title={`Model call retried · attempt ${num(p.attempt)}`}
        >
          <span className="text-xs">
            {str(p.kind)}
            {p.status ? ` (HTTP ${str(p.status)})` : ""}: waited {num(p.wait_s).toFixed(2)} s with backoff and
            jitter. {str(p.detail)}
          </span>
        </Row>
      );
    case "fault_injected": {
      const f = ctx.faults.find((x) => x.name === str(p.fault));
      return (
        <Row
          key={item.key}
          at={at}
          tone="violet"
          icon={<FlaskConical className="size-3.5" />}
          title={`Fault armed · ${f?.title ?? str(p.fault)}`}
        >
          {f && (
            <span className="text-xs">
              {f.effect} <span className="text-zinc-500">Expected: {f.expected}</span>
            </span>
          )}
        </Row>
      );
    }
    case "policy_evaluated": {
      const allowed = p.allowed === true;
      const violations = arr<{ ref: string; rules: string[] }>(p.violations);
      return (
        <Row
          key={item.key}
          at={at}
          tone={allowed ? "accent" : "danger"}
          icon={allowed ? <ShieldCheck className="size-3.5" /> : <ShieldAlert className="size-3.5" />}
          title={
            <span className="flex flex-wrap items-center gap-2">
              <span className="font-medium">Policy evaluated</span>
              <Badge tone={allowed ? "accent" : "danger"}>{allowed ? "allowed" : "denied"}</Badge>
              {str(p.risk) && <Badge tone={riskTone(str(p.risk))}>risk {str(p.risk)}</Badge>}
              {num(p.approvals_required) > 0 && <Badge>{num(p.approvals_required)} approvals required</Badge>}
            </span>
          }
        >
          {violations.length > 0 && (
            <ul className="space-y-0.5 text-xs">
              {violations.map((v) => (
                <li key={v.ref}>
                  <Mono>{v.ref}</Mono> violates {v.rules.join(", ")}
                </li>
              ))}
            </ul>
          )}
        </Row>
      );
    }
    case "plan_rejected": {
      const actions = arr<{ ref: string; violations: Array<{ rule: string; name: string; detail: string }> }>(
        p.actions,
      );
      return (
        <Row
          key={item.key}
          at={at}
          tone="danger"
          icon={<ShieldAlert className="size-3.5" />}
          title={`Policy rejected the draft (revision ${num(p.revision)}); the agent must revise it`}
          testId="event-plan-rejected"
        >
          <ul className="space-y-1 text-xs">
            {actions.map((a) => (
              <li key={a.ref}>
                <Mono>{a.ref}</Mono>:{" "}
                {a.violations.map((v) => (
                  <span key={v.rule} className="mr-2">
                    <Badge tone="danger" mono>
                      {v.rule}
                    </Badge>{" "}
                    {v.name}: {v.detail}
                  </span>
                ))}
              </li>
            ))}
          </ul>
        </Row>
      );
    }
    case "plan_submitted":
      return (
        <Row
          key={item.key}
          at={at}
          tone="accent"
          icon={<FileCheck2 className="size-3.5" />}
          title={
            <span className="flex flex-wrap items-center gap-2">
              <span className="font-medium">Change request submitted</span>
              <Badge mono title={str(p.hash)}>
                plan {shortHash(str(p.hash))}
              </Badge>
            </span>
          }
        >
          <span className="text-xs">
            {num(p.actions)} action(s) ·{" "}
            <span className="text-emerald-300">{formatMonthly(num(p.total_monthly_savings))}</span> · risk{" "}
            {str(p.risk)} · {num(p.approvals_required)} approval(s) required
          </span>
        </Row>
      );
    case "question": {
      const options = arr<string>(p.options);
      const pending = ctx.pendingQuestionSeq === item.seq;
      return (
        <Row
          key={item.key}
          at={at}
          tone="info"
          icon={<MessageCircleQuestion className="size-3.5" />}
          title={<span className="font-medium">The agent asks: {str(p.text)}</span>}
          testId="event-question"
        >
          {pending && ctx.canAnswer ? (
            <AnswerForm options={options} onAnswer={ctx.onAnswer} busy={ctx.answering} />
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {options.map((o) => (
                <Badge key={o}>{o}</Badge>
              ))}
              {pending && <span className="text-xs text-sky-300">Waiting for the operator to answer.</span>}
            </div>
          )}
        </Row>
      );
    }
    case "answer":
      return (
        <Row
          key={item.key}
          at={at}
          icon={<MessageSquareReply className="size-3.5" />}
          title={
            <span>
              {ctx.personaName(personaKeyOf(str(p.by)) ?? "")} answered:{" "}
              <span className="text-zinc-100">“{str(p.text)}”</span>
            </span>
          }
        />
      );
    case "approval": {
      const approve = arr<string>(p.approve);
      const reject = arr<string>(p.reject);
      return (
        <Row
          key={item.key}
          at={at}
          tone="accent"
          icon={<PenLine className="size-3.5" />}
          title={
            <span>
              <span className="font-medium text-zinc-100">
                {str(p.name) || ctx.personaName(str(p.persona))}
              </span>{" "}
              countersigned
            </span>
          }
          testId="event-approval"
        >
          <span className="text-xs">
            approved {approve.length}
            {reject.length > 0 && (
              <>
                , rejected {reject.length} ({reject.join(", ")})
              </>
            )}
            {str(p.note) && <> · “{str(p.note)}”</>}
          </span>
        </Row>
      );
    }
    case "awaiting_more_approvals":
      return (
        <Row
          key={item.key}
          at={at}
          tone="warn"
          icon={<PenLine className="size-3.5" />}
          title={`${num(p.have)} of ${num(p.need)} approvals: waiting for another approver`}
        />
      );
    case "capability_minted":
      return (
        <Row
          key={item.key}
          at={at}
          tone="accent"
          icon={<KeyRound className="size-3.5" />}
          title="Execution capability minted"
        >
          <span className="text-xs">
            A signed, short-lived token scoped to {arr<string>(p.actions).length} approved action(s) and this
            plan hash, countersigned by{" "}
            {arr<string>(p.approvers)
              .map((a) => ctx.personaName(personaKeyOf(a) ?? a))
              .join(" and ")}
            {num(p.expires_at) > 0 && (
              <>; expires {formatTime(new Date(num(p.expires_at) * 1000).toISOString())}</>
            )}
            .
          </span>
        </Row>
      );
    case "execution_step": {
      const action = ctx.actions.get(str(p.action_id));
      const status = str(p.status);
      const attempts = num(p.attempts);
      return (
        <Row
          key={item.key}
          at={at}
          tone={status === "failed" ? "danger" : status === "compensated" ? "warn" : "accent"}
          icon={status === "failed" ? <XCircle className="size-3.5" /> : <Wrench className="size-3.5" />}
          title={
            <span className="flex flex-wrap items-center gap-2">
              <Mono className="text-zinc-400">{str(p.action_id)}</Mono>
              <Mono className="text-zinc-100">{providerApi(action?.provider, str(p.step), p.api)}</Mono>
              <Badge tone={stepTone(status)}>{status}</Badge>
              {attempts > 1 && (
                <Badge tone="warn" icon={<RefreshCw className="size-3" aria-hidden />}>
                  {attempts} attempts
                </Badge>
              )}
            </span>
          }
          testId="event-execution-step"
        >
          <span className="text-xs">
            {str(p.detail)} · <span className="text-zinc-500">idempotency key</span>{" "}
            <Mono className="text-[11px] text-zinc-400" title={str(p.idempotency_key)}>
              {shortHash(str(p.idempotency_key), 12)}
            </Mono>
          </span>
        </Row>
      );
    }
    case "verification": {
      const ok = p.verified === true;
      return (
        <Row
          key={item.key}
          at={at}
          tone={ok ? "accent" : "danger"}
          icon={ok ? <CheckCircle2 className="size-3.5" /> : <XCircle className="size-3.5" />}
          title={
            <span className="flex flex-wrap items-center gap-2">
              <Mono className="text-zinc-400">{str(p.action_id)}</Mono>
              <Mono className="text-zinc-100">{str(p.ref)}</Mono>
              <Badge tone={ok ? "accent" : "danger"}>{ok ? "verified" : "not verified"}</Badge>
            </span>
          }
        >
          <span className="text-xs">
            re-read state {str(p.state)}
            {str(p.sku) && <> · {str(p.sku)}</>}
          </span>
        </Row>
      );
    }
    case "report":
      return (
        <Row
          key={item.key}
          at={at}
          tone="accent"
          icon={<ScrollText className="size-3.5" />}
          title="Report written"
        >
          <p className="line-clamp-3 text-xs">
            <CitedText text={str(p.text)} evidence={ctx.evidence} />
          </p>
        </Row>
      );
    case "refusal":
      return (
        <Row
          key={item.key}
          at={at}
          tone="warn"
          icon={<ShieldAlert className="size-3.5" />}
          title="The model declined"
        >
          {str(p.text)}
        </Row>
      );
    default:
      return (
        <Row key={item.key} at={at} icon={<CircleDot className="size-3.5" />} title={humanize(item.type)}>
          <Json value={p} />
        </Row>
      );
  }
}

export function riskTone(risk: string): "accent" | "warn" | "danger" | "neutral" {
  if (risk === "low") return "accent";
  if (risk === "medium") return "warn";
  if (risk === "high") return "danger";
  return "neutral";
}

export function stepTone(status: string): "accent" | "warn" | "danger" | "info" | "neutral" {
  if (status === "done") return "accent";
  if (status === "replayed") return "info";
  if (status === "compensated") return "warn";
  if (status === "failed") return "danger";
  return "neutral";
}

export function Timeline({
  events,
  ...ctx
}: Omit<Ctx, "actions"> & { events: RunEvent[]; actions: PlanAction[] }) {
  const items = buildTimeline(events);
  const actions = new Map(ctx.actions.map((a) => [a.id, a]));
  if (items.length === 0) {
    return <p className="py-6 text-center text-sm text-zinc-500">Waiting for the first event…</p>;
  }
  return (
    <ol
      className="[&>li:last-child>span:first-child]:hidden"
      aria-label="Run timeline"
      data-testid="timeline"
    >
      {items.map((item) => renderItem(item, { ...ctx, actions }))}
    </ol>
  );
}
