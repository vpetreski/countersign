import { useQuery } from "@tanstack/react-query";
import { Check, FlaskConical, Info, Minus, X } from "lucide-react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Callout, Card, cx, EmptyState, Mono, Skeleton, td, th } from "../../components/ui";
import { isProblem, request } from "../../lib/api";
import { formatDateTime, formatRatio, humanize, shortHash } from "../../lib/format";
import type { EvalReport } from "../../lib/types";

export const CRITERIA = [
  "task_completion",
  "groundedness",
  "relevance",
  "tool_accuracy",
  "safety",
  "failure_handling",
] as const;

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

interface Loaded {
  report: EvalReport;
  sample: boolean;
}

async function loadReport(): Promise<Loaded | null> {
  try {
    return { report: await request<EvalReport>("/api/evals/latest"), sample: false };
  } catch (err) {
    if (isProblem(err, 404) && import.meta.env.DEV) {
      // Development falls back to the report the backend publishes, so the page can be reviewed offline.
      const published = await import("../../../../backend/countersign/evals/published/latest.json");
      return { report: published.default as unknown as EvalReport, sample: true };
    }
    if (isProblem(err, 404)) return null;
    throw err;
  }
}

export function modelLabel(model: EvalReport["model"]): string {
  if (!model) return "—";
  if (typeof model === "string") return model;
  return [model.name, model.provider && `(${model.provider})`, model.simulated ? "· simulated" : null]
    .filter(Boolean)
    .join(" ");
}

/** The criteria in report order, with the six known ones first. */
export function criteriaNames(report: EvalReport): string[] {
  const keys = Object.keys(report.criteria ?? {});
  return [
    ...CRITERIA.filter((c) => keys.includes(c)),
    ...keys.filter((k) => !(CRITERIA as readonly string[]).includes(k)),
  ];
}

function Interval({ mean, lo, hi }: { mean: number | null; lo: number | null; hi: number | null }) {
  return (
    <div className="relative h-4 w-full min-w-40" aria-hidden>
      <div className="absolute inset-x-0 top-1/2 h-px bg-zinc-800" />
      {[0.25, 0.5, 0.75].map((t) => (
        <div key={t} className="absolute top-1 bottom-1 w-px bg-zinc-800" style={{ left: `${t * 100}%` }} />
      ))}
      {lo !== null && hi !== null && (
        <div
          className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-emerald-500/30"
          style={{ left: `${lo * 100}%`, width: `${Math.max((hi - lo) * 100, 0.5)}%` }}
        />
      )}
      {mean !== null && (
        <div
          className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-zinc-950 bg-emerald-400"
          style={{ left: `${mean * 100}%` }}
        />
      )}
    </div>
  );
}

function CriteriaTable({ report }: { report: EvalReport }) {
  const names = criteriaNames(report);
  if (names.length === 0) return <EmptyState title="The report has no criteria" />;
  return (
    <div className="scroll-thin -mx-5 overflow-x-auto px-5">
      <table className="w-full min-w-[720px] border-separate border-spacing-0" data-testid="criteria-table">
        <caption className="sr-only">Criteria with means and 95% confidence intervals</caption>
        <thead>
          <tr className="[&>th]:border-b [&>th]:border-zinc-800">
            <th className={th}>Criterion</th>
            <th className={cx(th, "text-right")}>Mean</th>
            <th className={th}>95% interval</th>
            <th className={cx(th, "w-[34%]")}>
              <span className="flex justify-between">
                <span>0</span>
                <span>1</span>
              </span>
            </th>
            <th className={cx(th, "text-right")}>Cases passing every trial</th>
          </tr>
        </thead>
        <tbody>
          {names.map((name) => {
            const c = report.criteria?.[name] ?? {};
            const mean = num(c.mean);
            const lo = num(c.ci95?.[0]);
            const hi = num(c.ci95?.[1]);
            const wl = num(c.cases_all_pass_wilson?.[0]);
            const wh = num(c.cases_all_pass_wilson?.[1]);
            return (
              <tr
                key={name}
                className="[&>td]:border-b [&>td]:border-zinc-800/70"
                data-testid="criterion-row"
              >
                <td className={td}>
                  <span className="font-medium">{humanize(name)}</span>
                  <Mono className="block text-[11px] text-zinc-500">{name}</Mono>
                </td>
                <td className={cx(td, "text-right font-mono tabular-nums")}>{formatRatio(mean)}</td>
                <td className={cx(td, "font-mono text-xs text-zinc-400")}>
                  {lo !== null && hi !== null ? `${formatRatio(lo)} – ${formatRatio(hi)}` : "—"}
                </td>
                <td className={td}>
                  <Interval mean={mean} lo={lo} hi={hi} />
                  {c.ci_method && <span className="mt-1 block text-[11px] text-zinc-500">{c.ci_method}</span>}
                </td>
                <td className={cx(td, "text-right text-xs")}>
                  <span className="font-mono">
                    {c.cases_all_pass ?? "—"}
                    {report.n_cases ? ` / ${report.n_cases}` : ""}
                  </span>
                  {wl !== null && wh !== null && (
                    <span className="block text-[11px] text-zinc-500">
                      Wilson {formatRatio(wl, 0)} – {formatRatio(wh, 0)}
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PassHatK({ report }: { report: EvalReport }) {
  const data = Object.entries(report.pass_hat_k ?? {})
    .map(([k, v]) => ({ k: Number(k), value: num(v) }))
    .filter((d) => Number.isFinite(d.k) && d.value !== null)
    .sort((a, b) => a.k - b.k);
  if (data.length === 0) return <EmptyState title="No pass^k data" />;
  return (
    <div
      className="h-56"
      role="img"
      aria-label={`pass^k: ${data.map((d) => `k=${d.k} ${formatRatio(d.value)}`).join(", ")}`}
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="#27272a" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="k"
            stroke="#52525b"
            fontSize={11}
            tickLine={false}
            axisLine={false}
            tickFormatter={(k: number) => `k=${k}`}
          />
          <YAxis
            domain={[0, 1]}
            stroke="#52525b"
            fontSize={11}
            tickLine={false}
            axisLine={false}
            width={40}
            tickFormatter={(v: number) => `${Math.round(v * 100)}%`}
          />
          <Tooltip
            contentStyle={{
              background: "rgba(24,24,27,0.96)",
              border: "1px solid #3f3f46",
              borderRadius: 10,
              fontSize: 12,
            }}
            formatter={(v) => [formatRatio(Number(v)), "pass^k"]}
            labelFormatter={(k) => `k = ${k}`}
          />
          <Line
            type="monotone"
            dataKey="value"
            stroke="#34d399"
            strokeWidth={2}
            dot={{ r: 3, fill: "#34d399" }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function MutationMatrix({ report }: { report: EvalReport }) {
  const mutants = Object.entries(report.mutation?.mutants ?? {});
  const criteria = criteriaNames(report);
  const cols = criteria.length ? criteria : [...CRITERIA];
  const n = report.n_cases ?? report.cases?.length ?? 0;
  if (mutants.length === 0) return <EmptyState title="No mutants in this report" />;
  return (
    <div className="space-y-4">
      <div className="scroll-thin -mx-5 overflow-x-auto px-5">
        <table
          className="w-full min-w-[760px] border-separate border-spacing-1"
          data-testid="mutation-matrix"
        >
          <caption className="sr-only">
            Mutation matrix: discordant cases per mutant and criterion; caught and expected cells are marked
          </caption>
          <thead>
            <tr>
              <th className={th}>Mutant</th>
              {cols.map((c) => (
                <th key={c} className={cx(th, "text-center normal-case")}>
                  {humanize(c)}
                </th>
              ))}
              <th className={cx(th, "text-center")}>Result</th>
            </tr>
          </thead>
          <tbody>
            {mutants.map(([name, m]) => {
              const caught = new Set(m.caught_by ?? []);
              return (
                <tr key={name} data-testid="mutant-row">
                  <td className="py-1 pr-3 align-middle">
                    <Mono className="text-zinc-100">{name}</Mono>
                    {m.description && (
                      <span className="block max-w-[260px] text-xs text-zinc-400">{m.description}</span>
                    )}
                  </td>
                  {cols.map((c) => {
                    const flips = num(m.discordant_cases?.[c]);
                    const score = num(m.scores?.[c]);
                    const p = num(m.p_value?.[c]);
                    const isCaught = caught.has(c);
                    const expected = m.expected_grader === c;
                    const intensity = flips !== null && n > 0 ? flips / n : score !== null ? 1 - score : 0;
                    const label = flips !== null ? String(flips) : score !== null ? score.toFixed(2) : "—";
                    return (
                      <td
                        key={c}
                        className={cx(
                          "h-12 min-w-16 rounded-md text-center align-middle font-mono text-xs",
                          isCaught && "ring-1 ring-emerald-400/70",
                          expected && "outline-1 outline-offset-2 outline-dashed outline-zinc-400",
                        )}
                        style={{ background: `rgba(239, 68, 68, ${0.05 + intensity * 0.5})` }}
                        title={[
                          `${name} · ${c}`,
                          flips !== null ? `${flips} of ${n} cases flipped from pass to fail` : null,
                          score !== null ? `score ${score}` : null,
                          p !== null ? `p = ${p.toExponential(1)}` : null,
                          expected ? "the grader this mutant targets" : null,
                          m.example?.[c] ? `e.g. ${m.example[c]}` : null,
                        ]
                          .filter(Boolean)
                          .join("\n")}
                        data-caught={isCaught || undefined}
                        data-expected={expected || undefined}
                      >
                        <span className="flex flex-col items-center leading-tight">
                          <span className="inline-flex items-center gap-1 text-zinc-100">
                            {label}
                            {isCaught && (
                              <>
                                <Check className="size-3.5 text-emerald-300" aria-hidden />
                                <span className="sr-only">caught</span>
                              </>
                            )}
                          </span>
                          {expected && (
                            <span className="text-[9px] uppercase tracking-wide text-zinc-400">target</span>
                          )}
                        </span>
                      </td>
                    );
                  })}
                  <td className="text-center align-middle">
                    {caught.size === 0 ? (
                      <Badge tone="danger">survived</Badge>
                    ) : m.caught_by_expected === false ? (
                      <Badge tone="warn">caught elsewhere</Badge>
                    ) : (
                      <Badge tone="accent">caught</Badge>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-zinc-500">
        Each cell counts the cases that passed for the reference agent and failed for the mutant (discordant
        cases, out of {n}). A tick marks a statistically significant catch; the dashed outline marks the
        grader the mutant was built to trip.
      </p>
      <details className="group rounded-lg border border-zinc-800 bg-zinc-950/30 px-3 py-2">
        <summary className="text-xs text-zinc-400 hover:text-zinc-200">
          Why each mutant was caught: one example per grader
        </summary>
        <ul className="mt-2 space-y-2 text-xs">
          {mutants.flatMap(([name, m]) =>
            Object.entries(m.example ?? {}).map(([criterion, text]) => (
              <li key={`${name}-${criterion}`} className="leading-relaxed">
                <Mono className="text-zinc-200">{name}</Mono>{" "}
                <span className="text-zinc-500">· {humanize(criterion)}:</span>{" "}
                <span className="break-words text-zinc-300">{text}</span>
              </li>
            )),
          )}
        </ul>
      </details>
    </div>
  );
}
function Calibration({ report }: { report: EvalReport }) {
  const c = report.calibration;
  if (!c || (!c.truth && !c.estimate)) return null;
  const truth = num(c.truth?.task_completion_mean);
  const est = num(c.estimate?.task_completion_mean);
  const lo = num(c.estimate?.ci95?.[0]);
  const hi = num(c.estimate?.ci95?.[1]);
  const ks = [
    ...new Set([...Object.keys(c.truth?.pass_hat_k ?? {}), ...Object.keys(c.estimate?.pass_hat_k ?? {})]),
  ]
    .map(Number)
    .filter(Number.isFinite)
    .sort((a, b) => a - b);
  const data = ks.map((k) => ({
    k,
    truth: num(c.truth?.pass_hat_k?.[String(k)]),
    estimate: num(c.estimate?.pass_hat_k?.[String(k)]),
  }));
  const r = c.realized;
  const cov = c.coverage;
  return (
    <Card
      title="Calibration"
      subtitle={
        c.simulator
          ? `Does the estimator recover a known truth? Simulator: ${c.simulator}`
          : "Estimator against a known truth"
      }
      data-testid="calibration"
    >
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-3">
            <div className="rounded-lg border border-zinc-800 bg-zinc-950/40 p-3">
              <p className="text-[11px] text-zinc-500">Truth</p>
              <p className="font-mono text-lg text-zinc-100">{formatRatio(truth)}</p>
              <p className="text-[11px] text-zinc-500">task completion</p>
            </div>
            <div className="rounded-lg border border-zinc-800 bg-zinc-950/40 p-3">
              <p className="text-[11px] text-zinc-500">Estimate</p>
              <p className="font-mono text-lg text-zinc-100">{formatRatio(est)}</p>
              <p className="text-[11px] text-zinc-500">
                95% {lo !== null && hi !== null ? `${formatRatio(lo)} – ${formatRatio(hi)}` : "—"}
              </p>
            </div>
            <div className="rounded-lg border border-zinc-800 bg-zinc-950/40 p-3">
              <p className="text-[11px] text-zinc-500">Interval covers truth</p>
              <p className="font-mono text-lg text-zinc-100">
                {c.covered === undefined ? "—" : c.covered ? "yes" : "no"}
              </p>
              <p className="text-[11px] text-zinc-500">
                noise {c.noise ?? "—"} · {c.trials ?? "—"} trials · {c.cases ?? "—"} cases
              </p>
            </div>
          </div>
          {c.covered === false && r && (
            <div
              className="rounded-lg border border-sky-500/30 bg-sky-500/[0.06] px-3.5 py-3 text-sm text-sky-100"
              data-testid="calibration-miss"
            >
              <p className="font-medium">
                This draw's interval missed the truth, as about one draw in twenty should.
              </p>
              <p className="mt-1 text-sky-100/80">
                Realized slip rate {formatRatio(r.rate)} ({r.slips ?? "—"} of {r.chances ?? "—"}) against a
                configured {formatRatio(c.noise)}; z = {num(r.z)?.toFixed(2) ?? "—"}. {r.note}
              </p>
            </div>
          )}
          {cov && (
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:grid-cols-3">
              <div>
                <dt className="text-zinc-500">Mean interval coverage</dt>
                <dd className="font-mono text-zinc-200">
                  {formatRatio(cov.mean_interval_coverage)}{" "}
                  <span className="text-zinc-500">(nominal {formatRatio(cov.nominal, 0)})</span>
                </dd>
              </div>
              <div>
                <dt className="text-zinc-500">All-pass Wilson coverage</dt>
                <dd className="font-mono text-zinc-200">{formatRatio(cov.all_pass_wilson_coverage)}</dd>
              </div>
              <div>
                <dt className="text-zinc-500">Replicates</dt>
                <dd className="font-mono text-zinc-200">
                  {cov.replicates ?? "—"}{" "}
                  <span className="text-zinc-500">± {formatRatio(cov.monte_carlo_se)} MC s.e.</span>
                </dd>
              </div>
              <div>
                <dt className="text-zinc-500">Mean interval width</dt>
                <dd className="font-mono text-zinc-200">{formatRatio(cov.mean_interval_width)}</dd>
              </div>
              {c.other_criteria_unaffected !== undefined && (
                <div>
                  <dt className="text-zinc-500">Other criteria unaffected</dt>
                  <dd className="font-mono text-zinc-200">{c.other_criteria_unaffected ? "yes" : "no"}</dd>
                </div>
              )}
            </dl>
          )}
        </div>
        {data.length > 0 && (
          <div className="h-56" role="img" aria-label="pass^k on the simulator: truth against the estimate">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
                <CartesianGrid stroke="#27272a" strokeDasharray="3 3" vertical={false} />
                <XAxis
                  dataKey="k"
                  stroke="#52525b"
                  fontSize={11}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(k: number) => `k=${k}`}
                />
                <YAxis
                  domain={[0, 1]}
                  stroke="#52525b"
                  fontSize={11}
                  tickLine={false}
                  axisLine={false}
                  width={40}
                  tickFormatter={(v: number) => `${Math.round(v * 100)}%`}
                />
                <Tooltip
                  contentStyle={{
                    background: "rgba(24,24,27,0.96)",
                    border: "1px solid #3f3f46",
                    borderRadius: 10,
                    fontSize: 12,
                  }}
                  formatter={(v, name) => [formatRatio(Number(v)), name === "truth" ? "Truth" : "Estimate"]}
                  labelFormatter={(k) => `k = ${k}`}
                />
                <Legend
                  formatter={(v: string) => (v === "truth" ? "Truth" : "Estimate")}
                  wrapperStyle={{ fontSize: 12, color: "#a1a1aa" }}
                  iconSize={8}
                />
                <Line
                  type="monotone"
                  dataKey="truth"
                  stroke="#a1a1aa"
                  strokeDasharray="4 3"
                  strokeWidth={2}
                  dot={{ r: 2 }}
                  isAnimationActive={false}
                />
                <Line
                  type="monotone"
                  dataKey="estimate"
                  stroke="#34d399"
                  strokeWidth={2}
                  dot={{ r: 3 }}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </Card>
  );
}

function CasesTable({ report }: { report: EvalReport }) {
  const cases = report.cases ?? [];
  const criteria = criteriaNames(report);
  if (cases.length === 0) return <EmptyState title="No cases in this report" />;
  return (
    <div className="scroll-thin -mx-5 overflow-x-auto px-5">
      <table className="w-full min-w-[860px] border-separate border-spacing-0" data-testid="cases-table">
        <thead>
          <tr className="[&>th]:border-b [&>th]:border-zinc-800">
            <th className={th}>Case</th>
            {criteria.map((c) => (
              <th key={c} className={cx(th, "text-center normal-case")}>
                {humanize(c)}
              </th>
            ))}
            <th className={cx(th, "text-center")}>All trials</th>
          </tr>
        </thead>
        <tbody>
          {cases.map((c) => (
            <tr key={c.id} className="[&>td]:border-b [&>td]:border-zinc-800/70">
              <td className={td}>
                <Mono className="text-zinc-100">{c.id}</Mono>
                {c.title && <span className="block text-xs text-zinc-400">{c.title}</span>}
                <span className="mt-1 flex flex-wrap gap-1">
                  {(c.tags ?? []).map((t) => (
                    <Badge key={t}>{t}</Badge>
                  ))}
                </span>
              </td>
              {criteria.map((k) => {
                const trials = c.outcomes?.[k] ?? [];
                return (
                  <td key={k} className={cx(td, "text-center")}>
                    <span
                      className="inline-flex gap-0.5"
                      role="img"
                      aria-label={`${k}: ${trials.filter((t) => t === 1).length} of ${trials.length} trials passed`}
                    >
                      {trials.length === 0 ? (
                        <Minus className="size-3.5 text-zinc-600" aria-hidden />
                      ) : (
                        trials.map((t, i) => (
                          <span
                            // biome-ignore lint/suspicious/noArrayIndexKey: trials are positional
                            key={i}
                            className={cx(
                              "inline-flex size-4 items-center justify-center rounded-sm",
                              t === 1 ? "bg-emerald-500/20 text-emerald-300" : "bg-red-500/20 text-red-300",
                            )}
                          >
                            {t === 1 ? (
                              <Check className="size-3" aria-hidden />
                            ) : (
                              <X className="size-3" aria-hidden />
                            )}
                          </span>
                        ))
                      )}
                    </span>
                  </td>
                );
              })}
              <td className={cx(td, "text-center")}>
                {c.passed_all ? <Badge tone="accent">pass</Badge> : <Badge tone="danger">fail</Badge>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function EvalReportView({ report, isSample }: { report: EvalReport; isSample: boolean }) {
  const regressions = report.baseline?.regressions ?? [];
  const improvements = report.baseline?.improvements ?? [];
  return (
    <div className="space-y-6" data-testid="evals-view">
      {isSample && (
        <Callout tone="info" icon={<Info className="size-5" aria-hidden />} title="Sample data">
          The API returned 404 for the report, so development shows the report the backend publishes in its
          source tree.
        </Callout>
      )}
      <section
        aria-label="How to read this report"
        className="rounded-xl border border-amber-500/30 bg-gradient-to-b from-amber-500/[0.08] to-transparent px-5 py-4"
        data-testid="honesty"
      >
        <p className="text-xs font-semibold uppercase tracking-wide text-amber-300">Read this first</p>
        <p className="mt-1 text-sm leading-relaxed text-zinc-100">
          {report.honesty ?? "This report carries no honesty statement."}
        </p>
        <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-400">
          <span>model {modelLabel(report.model)}</span>
          <span>{report.n_cases ?? report.cases?.length ?? "—"} cases</span>
          <span>{report.trials ?? "—"} trials each</span>
          <span>generated {formatDateTime(report.generated_at)}</span>
          {report.fingerprint?.combined && (
            <span>
              fingerprint <Mono className="text-[11px]">{shortHash(report.fingerprint.combined, 12)}</Mono>
            </span>
          )}
          {report.overall?.cases_all_pass !== undefined && (
            <span>
              {report.overall.cases_all_pass} of {report.n_cases ?? "—"} cases pass every criterion on every
              trial
            </span>
          )}
          {report.runs?.total !== undefined && (
            <span>
              {report.runs.total} runs in {report.runs.wall_seconds ?? "—"} s
            </span>
          )}
          {report.mde && (
            <span>
              minimum detectable effect: {report.mde.discordant_flips ?? "—"} discordant flips at α=
              {report.mde.alpha ?? "—"}
            </span>
          )}
        </p>
      </section>

      <Card
        title="Criteria"
        icon={<FlaskConical className="size-4" aria-hidden />}
        subtitle="Mean over cases and trials, with 95% intervals"
      >
        <CriteriaTable report={report} />
        {(regressions.length > 0 || improvements.length > 0) && (
          <p className="mt-3 flex flex-wrap gap-2 text-xs text-zinc-400">
            Against the baseline:
            {regressions.map((r) => (
              <Badge key={String(r)} tone="danger">
                regression {typeof r === "string" ? r : JSON.stringify(r)}
              </Badge>
            ))}
            {improvements.map((r) => (
              <Badge key={String(r)} tone="accent">
                improvement {typeof r === "string" ? r : JSON.stringify(r)}
              </Badge>
            ))}
          </p>
        )}
      </Card>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <Card
          title="pass^k"
          subtitle="Probability that all k trials of a case pass: the reliability a user feels"
        >
          <PassHatK report={report} />
        </Card>
        <Card
          title="Mutation testing"
          subtitle={
            <>
              Deliberately broken agents; a grader that scores a mutant down catches it
              {num(report.mutation?.score) !== null && <> · score {formatRatio(report.mutation?.score)}</>}
              {num(report.mutation?.reference_false_positives) !== null && (
                <> · {report.mutation?.reference_false_positives} false positives on the reference</>
              )}
            </>
          }
        >
          <MutationMatrix report={report} />
        </Card>
      </div>

      <Calibration report={report} />

      <Card title="Cases" subtitle="Every trial of every case, per criterion">
        <CasesTable report={report} />
      </Card>
    </div>
  );
}

export default function EvalsView() {
  const q = useQuery({ queryKey: ["evals"], queryFn: loadReport, staleTime: 5 * 60_000, retry: 1 });
  if (q.isPending) {
    return (
      <div className="space-y-6" aria-busy="true">
        <Skeleton className="h-24" />
        <Skeleton className="h-72" />
      </div>
    );
  }
  if (q.isError) {
    return (
      <EmptyState title="The evaluation report could not be loaded">{(q.error as Error).message}</EmptyState>
    );
  }
  if (!q.data) {
    return (
      <EmptyState
        icon={<FlaskConical className="size-7" />}
        title="No evaluation report is published yet"
        className="py-16"
        data-testid="evals-empty"
      >
        The evaluation suite runs offline against the same runtime and publishes its report here. Until then
        there is nothing to show, and this page does not invent one.
      </EmptyState>
    );
  }
  return <EvalReportView report={q.data.report} isSample={q.data.sample} />;
}
