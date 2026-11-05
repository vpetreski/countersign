import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowDown,
  CircleStop,
  FlaskConical,
  Gauge,
  ListTree,
  MessageCircleQuestion,
  PenLine,
  Play,
  ScrollText,
  Users,
  Waypoints,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { CitedSummary } from "../../components/Citations";
import { NotProposedTable } from "../../components/NotProposed";
import { useToast } from "../../components/toast";
import { Badge, Button, Callout, Card, cx, EmptyState, Mono, Skeleton } from "../../components/ui";
import { runKey, useConfig, useRun, useRunEvents } from "../../hooks";
import { isProblem } from "../../lib/api";
import { AutoAdvancer, shouldAdvance } from "../../lib/autoAdvance";
import { formatDateTime, formatMonthly } from "../../lib/format";
import { countersignState, nextApprover, operatorOf, personaKeyOf, personaName } from "../../lib/personas";
import {
  pendingQuestionSeq,
  statusLabel,
  statusTone,
  stepperModel,
  visitedStatuses,
} from "../../lib/timeline";
import type { RunView, Verdict } from "../../lib/types";
import { useUrlParam } from "../../lib/url";
import { useSession } from "../../session";
import { ChangeRequest } from "./ChangeRequest";
import { Execution } from "./Execution";
import { BudgetMeters } from "./RunList";
import { Stepper } from "./Stepper";
import { Timeline } from "./Timeline";
import { TraceWaterfall } from "./TraceWaterfall";

const TONE_TO_BADGE = {
  accent: "accent",
  warn: "warn",
  danger: "danger",
  info: "info",
  neutral: "neutral",
} as const;

/**
 * Keeps the run moving while the active persona may advance it; stops at human waits and terminal states.
 * One controller per panel, so a re-run effect (React's development double mount, a persona switch) never
 * puts a second request in flight.
 */
function useAutoAdvance(
  run: RunView | undefined,
  onRun: (r: RunView) => void,
  onError: (e: unknown) => void,
) {
  const { api, persona } = useSession();
  const [active, setActive] = useState(false);
  const runRef = useRef(run);
  runRef.current = run;
  const handlers = useRef({ onRun, onError });
  handlers.current = { onRun, onError };
  const controller = useRef<AutoAdvancer | null>(null);
  const advancing = shouldAdvance(run);
  const runId = run?.id;
  // biome-ignore lint/correctness/useExhaustiveDependencies: restart only when the run, persona or advancing state changes
  useEffect(() => {
    const current = runRef.current;
    if (!current || !advancing) return;
    controller.current ??= new AutoAdvancer({
      advance: () =>
        api<RunView>(`/api/runs/${runRef.current?.id ?? current.id}/advance`, { method: "POST" }),
      onRun: (r) => handlers.current.onRun(r),
      onError: (e) => handlers.current.onError(e),
      onIdle: () => setActive(false),
    });
    const ctl = controller.current;
    if (ctl.start(current)) setActive(true);
    return () => {
      ctl.stop();
      setActive(false);
    };
  }, [runId, persona.key, advancing, api]);
  return active;
}

export function RunPanel({ runId }: { runId: string }) {
  const { api, persona, personas, setPersona, workspace } = useSession();
  const ws = workspace.workspace_id;
  const qc = useQueryClient();
  const toast = useToast();
  const config = useConfig();
  const [, setRunParam] = useUrlParam("run");
  const [panel, setPanel] = useUrlParam("panel");
  const [auto, setAuto] = useState(false);
  const runQ = useRun(runId, { poll: !auto });
  const run = runQ.data;
  const personaRef = useRef(persona.key);
  personaRef.current = persona.key;

  const applyRun = useCallback(
    (r: RunView) => {
      qc.setQueryData<RunView>(runKey(ws, personaRef.current, r.id), (cur) =>
        cur && cur.version > r.version ? cur : r,
      );
    },
    [qc, ws],
  );
  const advancing = useAutoAdvance(run, applyRun, (err) => {
    toast.problem(err, "The run could not advance");
    void runQ.refetch();
  });
  useEffect(() => setAuto(advancing), [advancing]);

  const events = useRunEvents(runId, run?.version);

  // When the status moves, everything derived from it is stale: lists, the audit log, the dashboard.
  const prevStatus = useRef<string | null>(null);
  useEffect(() => {
    if (!run) return;
    const prev = prevStatus.current;
    prevStatus.current = run.status;
    if (!prev || prev === run.status) return;
    void qc.invalidateQueries({ queryKey: ["runs", ws] });
    void qc.invalidateQueries({ queryKey: ["audit", ws] });
    void qc.invalidateQueries({
      predicate: (q) =>
        q.queryKey[0] === "run" && q.queryKey[3] === run.id && q.queryKey[2] !== personaRef.current,
    });
    if (run.terminal) {
      void qc.invalidateQueries({ queryKey: ["dashboard", ws] });
      void qc.invalidateQueries({ queryKey: ["findings", ws] });
      void qc.invalidateQueries({ queryKey: ["resource", ws] });
    }
  }, [run, qc, ws]);

  const decide = useMutation({
    mutationFn: (v: { decisions: Record<string, Verdict>; note: string }) =>
      api<RunView>(`/api/runs/${runId}/decision`, {
        method: "POST",
        idempotent: true,
        body: {
          plan_hash: run?.plan?.hash,
          decisions: Object.entries(v.decisions).map(([action_id, decision]) => ({ action_id, decision })),
          note: v.note || null,
        },
      }),
    onSuccess: (r) => {
      applyRun(r);
      const signers = r.plan?.approvals_received ?? 0;
      const need = r.plan?.approvals_required ?? 1;
      toast.push({
        tone: "success",
        title: `Countersigned by ${persona.name}`,
        detail:
          r.status === "awaiting_approval"
            ? `${signers} of ${need} approvals. Another approver must countersign.`
            : r.status === "rejected"
              ? "Every action was rejected; nothing will change."
              : "The capability is minted; execution starts now.",
      });
    },
    onError: (err) => {
      toast.problem(err, "The countersign was not recorded");
      void runQ.refetch();
    },
  });

  const answer = useMutation({
    mutationFn: (text: string) =>
      api<RunView>(`/api/runs/${runId}/input`, { method: "POST", body: { answer: text } }),
    onSuccess: applyRun,
    onError: (err) => toast.problem(err, "The answer was not recorded"),
  });

  const cancel = useMutation({
    mutationFn: () => api<RunView>(`/api/runs/${runId}/cancel`, { method: "POST" }),
    onSuccess: applyRun,
    onError: (err) => toast.problem(err, "The run could not be cancelled"),
  });

  const resume = useMutation({
    mutationFn: () => api<RunView>(`/api/runs/${runId}/advance`, { method: "POST" }),
    onSuccess: applyRun,
    onError: (err) => toast.problem(err, "The run could not advance"),
  });

  if (runQ.isError && isProblem(runQ.error, 404)) {
    return (
      <EmptyState title="This run is not in this workspace" className="bg-zinc-900/30">
        It may belong to an expired workspace.{" "}
        <button type="button" className="text-emerald-300 underline" onClick={() => setRunParam(null)}>
          Start a new run
        </button>
      </EmptyState>
    );
  }
  if (!run) {
    return (
      <Card>
        <div className="space-y-3" role="status" aria-busy="true" aria-label="Loading the run">
          <Skeleton className="h-5 w-2/3" />
          <Skeleton className="h-3 w-1/3" />
          <Skeleton className="mt-4 h-8" />
        </div>
      </Card>
    );
  }

  const steps = stepperModel(run.status, visitedStatuses(events));
  const sign = countersignState(run, persona);
  const approver = nextApprover(run, personas);
  const operator = operatorOf(personas);
  const pendingQ = pendingQuestionSeq(events, run.status);
  const faults = config.data?.faults ?? [];
  const isOperatorRun = personaKeyOf(run.created_by) === persona.key;
  const stalled =
    !run.terminal &&
    !run.can.advance &&
    ["created", "planning", "executing", "verifying"].includes(run.status);
  const tab = panel === "trace" ? "trace" : "timeline";

  return (
    <div className="space-y-6" id="run-panel" data-testid="run-panel" data-run={run.id}>
      <Card bodyClassName="space-y-5 px-5 py-5">
        <div className="flex flex-wrap items-start gap-4">
          <div className="min-w-0 flex-1 basis-72">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={TONE_TO_BADGE[statusTone(run.status)]} className="text-xs">
                {statusLabel(run.status)}
              </Badge>
              <span
                className="sr-only"
                data-testid="run-status"
                data-status={run.status}
                aria-live="polite"
                role="status"
              >
                Run status: {statusLabel(run.status)}
              </span>
              {advancing && <span className="text-xs text-sky-300">advancing…</span>}
              {run.faults.map((f) => (
                <Badge key={f} tone="violet" icon={<FlaskConical className="size-3" aria-hidden />}>
                  {faults.find((x) => x.name === f)?.title ?? f}
                </Badge>
              ))}
            </div>
            <h2 className="mt-2 text-lg font-semibold leading-snug text-zinc-50">“{run.request}”</h2>
            <p className="mt-1 flex flex-wrap gap-x-3 text-xs text-zinc-500">
              <Mono className="text-[11px] text-zinc-500">{run.id}</Mono>
              <span>{formatDateTime(run.created_at)}</span>
              <span>requested by {personaName(personaKeyOf(run.created_by), personas)}</span>
              <span className="font-mono text-[11px]">{run.model}</span>
            </p>
          </div>
          <div className="flex items-center gap-2">
            {run.can.advance && !advancing && shouldAdvance(run) && (
              <Button
                size="sm"
                icon={<Play className="size-3.5" aria-hidden />}
                loading={resume.isPending}
                onClick={() => resume.mutate()}
              >
                Resume
              </Button>
            )}
            {run.can.cancel && (
              <Button
                size="sm"
                variant="ghost"
                icon={<CircleStop className="size-3.5" aria-hidden />}
                loading={cancel.isPending}
                onClick={() => cancel.mutate()}
              >
                Cancel run
              </Button>
            )}
          </div>
        </div>
        <Stepper steps={steps} />
      </Card>

      {run.status === "awaiting_approval" &&
        run.plan &&
        (sign.can ? (
          <Callout
            tone="accent"
            icon={<PenLine className="size-5" aria-hidden />}
            title={`${persona.name}, your countersign is needed`}
            data-testid="countersign-callout"
            actions={
              <Button
                variant="accent-soft"
                size="sm"
                icon={<ArrowDown className="size-3.5" aria-hidden />}
                onClick={() =>
                  document
                    .getElementById("countersign-anchor")
                    ?.scrollIntoView({ behavior: "smooth", block: "center" })
                }
              >
                Review and countersign
              </Button>
            }
          >
            {run.plan.approvals_received} of {run.plan.approvals_required} approvals so far. Approve or reject
            each action, then countersign.
          </Callout>
        ) : (
          <Callout
            tone="warn"
            icon={<Users className="size-5" aria-hidden />}
            title="Waiting for a countersign"
            data-testid="waiting-callout"
            actions={
              approver && (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setPersona(approver.key)}
                  data-testid="switch-approver"
                >
                  Switch to {approver.name} (approver)
                </Button>
              )
            }
          >
            {run.plan.approvals_received} of {run.plan.approvals_required} approvals.{" "}
            {isOperatorRun
              ? `${persona.name} requested this change and cannot approve it. `
              : sign.reason
                ? `${sign.reason} `
                : ""}
            Nothing changes until approvers countersign.
          </Callout>
        ))}

      {run.status === "awaiting_input" && (
        <Callout
          tone="info"
          icon={<MessageCircleQuestion className="size-5" aria-hidden />}
          title="The agent needs an answer"
          data-testid="question-callout"
          actions={
            run.can.answer ? (
              <Button
                size="sm"
                variant="secondary"
                onClick={() =>
                  document
                    .querySelector('[data-testid="answer-form"]')
                    ?.scrollIntoView({ behavior: "smooth", block: "center" })
                }
              >
                Answer in the timeline
              </Button>
            ) : (
              operator && (
                <Button size="sm" variant="secondary" onClick={() => setPersona(operator.key)}>
                  Switch to {operator.name} (operator)
                </Button>
              )
            )
          }
        >
          {run.question?.text}
        </Callout>
      )}

      {stalled && operator && (
        <Callout
          tone="info"
          icon={<Play className="size-5" aria-hidden />}
          title={`${persona.name} can watch this run but not drive it`}
          actions={
            <Button size="sm" variant="secondary" onClick={() => setPersona(operator.key)}>
              Switch to {operator.name} (operator)
            </Button>
          }
        >
          Viewers read runs; an operator or approver advances them.
        </Callout>
      )}

      {run.degraded.length > 0 && (
        <Callout
          tone="warn"
          icon={<AlertTriangle className="size-5" aria-hidden />}
          title="Degraded run"
          data-testid="degraded-banner"
          role="status"
        >
          <ul className="space-y-0.5">
            {run.degraded.map((d) => (
              <li key={d}>{d}</li>
            ))}
          </ul>
        </Callout>
      )}

      {run.error && (
        <Callout
          tone="danger"
          icon={<AlertTriangle className="size-5" aria-hidden />}
          title="The run stopped"
          role="alert"
        >
          {run.error}
        </Callout>
      )}

      {run.plan && (
        <div id="countersign-anchor">
          <ChangeRequest
            run={run}
            persona={persona}
            personas={personas}
            rules={config.data?.policy.rules ?? []}
            deciding={decide.isPending}
            onDecide={(decisions, note) => decide.mutate({ decisions, note })}
          />
        </div>
      )}

      <Execution run={run} />

      {run.report && (
        <Card
          title="Report"
          icon={<ScrollText className="size-4" aria-hidden />}
          data-testid="report"
          actions={
            run.realized_monthly_savings ? (
              <Badge tone="accent">{formatMonthly(run.realized_monthly_savings)} realized</Badge>
            ) : undefined
          }
        >
          <CitedSummary
            text={run.report}
            evidence={run.evidence}
            hideExcluded={!run.plan && (run.not_proposed?.length ?? 0) > 0}
          />
          {!run.plan && (run.not_proposed?.length ?? 0) > 0 && (
            <div className="mt-5">
              <NotProposedTable items={run.not_proposed ?? []} evidence={run.evidence} />
            </div>
          )}
        </Card>
      )}

      <Card
        title="Activity"
        icon={<ListTree className="size-4" aria-hidden />}
        actions={
          <div
            role="tablist"
            aria-label="Activity views"
            className="inline-flex rounded-lg border border-zinc-800 p-0.5"
          >
            {(
              [
                ["timeline", "Timeline", <ListTree key="t" className="size-3.5" aria-hidden />],
                ["trace", "Trace", <Waypoints key="w" className="size-3.5" aria-hidden />],
              ] as const
            ).map(([k, label, icon]) => (
              <button
                key={k}
                type="button"
                role="tab"
                aria-selected={tab === k}
                aria-controls={`activity-${k}`}
                id={`tab-${k}`}
                onClick={() => setPanel(k === "timeline" ? null : k, "replace")}
                className={cx(
                  "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs transition",
                  tab === k ? "bg-zinc-800 text-zinc-100" : "text-zinc-400 hover:text-zinc-200",
                )}
              >
                {icon}
                {label}
              </button>
            ))}
          </div>
        }
      >
        <div role="tabpanel" id={`activity-${tab}`} aria-labelledby={`tab-${tab}`}>
          {tab === "timeline" ? (
            <Timeline
              events={events}
              evidence={run.evidence}
              actions={run.plan?.actions ?? []}
              faults={faults}
              pendingQuestionSeq={pendingQ}
              canAnswer={run.can.answer}
              answering={answer.isPending}
              onAnswer={(a) => answer.mutate(a)}
              personaName={(k) => personaName(k, personas)}
            />
          ) : (
            <TraceWaterfall runId={run.id} version={run.version} />
          )}
        </div>
      </Card>
    </div>
  );
}

export function RunBudgets({ runId }: { runId: string }) {
  const run = useRun(runId, { poll: false }).data;
  if (!run) return null;
  return (
    <Card
      title="Budgets"
      icon={<Gauge className="size-4" aria-hidden />}
      subtitle="Hard per-run limits enforced by the runtime"
    >
      <BudgetMeters budgets={run.budgets} />
    </Card>
  );
}
