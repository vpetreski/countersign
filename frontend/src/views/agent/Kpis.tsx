import { Activity, Boxes, Database, PiggyBank, Receipt, Sparkles, TrendingDown } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { AnimatedNumber } from "../../components/AnimatedNumber";
import { Badge, cx, Skeleton } from "../../components/ui";
import { useDashboard } from "../../hooks";
import { formatInt, formatMonthly, formatUsd, formatUsdDelta } from "../../lib/format";
import { useSession } from "../../session";

function Kpi({
  label,
  icon,
  children,
  sub,
  className,
  testId,
  value,
}: {
  label: string;
  icon: ReactNode;
  children: ReactNode;
  sub?: ReactNode;
  className?: string;
  testId?: string;
  value?: number;
}) {
  return (
    <div
      className={cx(
        "relative overflow-hidden rounded-xl border border-zinc-800/80 bg-zinc-900/40 px-4 py-3.5 animate-fade-in",
        className,
      )}
      data-testid={testId}
      data-value={value}
    >
      <p className="flex items-center gap-1.5 text-xs text-zinc-400">
        <span className="text-zinc-500">{icon}</span>
        {label}
      </p>
      <div className="mt-1.5 text-xl font-semibold tracking-tight text-zinc-50 tabular-nums">{children}</div>
      {sub && <div className="mt-1 text-xs text-zinc-400">{sub}</div>}
    </div>
  );
}

const BASELINE_KEY = "countersign.baseline";

/** The eligible savings when this workspace was fresh, to show what the workspace's runs changed. */
function useBaseline(workspaceId: string, eligible: number | undefined, changed: number): number | null {
  const [baseline, setBaseline] = useState<number | null>(() => {
    const raw = sessionStorage.getItem(BASELINE_KEY);
    if (!raw) return null;
    try {
      const v = JSON.parse(raw) as { ws: string; eligible: number };
      return v.ws === workspaceId ? v.eligible : null;
    } catch {
      return null;
    }
  });
  useEffect(() => {
    if (eligible === undefined) return;
    if (baseline === null && changed === 0) {
      sessionStorage.setItem(BASELINE_KEY, JSON.stringify({ ws: workspaceId, eligible }));
      setBaseline(eligible);
    }
  }, [workspaceId, eligible, changed, baseline]);
  return baseline;
}

export function Kpis() {
  const { workspace } = useSession();
  const { data, isPending, isError } = useDashboard();
  const changed = data?.changed_in_workspace.length ?? 0;
  const baseline = useBaseline(workspace.workspace_id, data?.waste.eligible_monthly_usd, changed);
  if (isPending || !data) {
    return (
      <div
        className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5"
        role="status"
        aria-busy="true"
        aria-label="Loading the estate"
      >
        {["a", "b", "c", "d", "e"].map((k) => (
          <div key={k} className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 px-4 py-3.5">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="mt-3 h-6 w-28" />
            <Skeleton className="mt-2 h-3 w-32" />
          </div>
        ))}
        {isError && <p className="col-span-full text-sm text-red-300">The dashboard could not be loaded.</p>}
      </div>
    );
  }
  const aws = data.spend.by_provider.aws ?? 0;
  const azure = data.spend.by_provider.azure ?? 0;
  const total = data.spend.total || aws + azure;
  const providers = data.resources.by_provider;
  const count = (p: string) => Object.values(providers[p] ?? {}).reduce((a, b) => a + b, 0);
  const pass = data.data_health.checks.pass ?? 0;
  const warn = data.data_health.checks.warn ?? 0;
  const fail = data.data_health.checks.fail ?? 0;
  const delta = baseline !== null ? data.waste.eligible_monthly_usd - baseline : 0;
  return (
    <div
      className={cx(
        "grid grid-cols-2 gap-3 md:grid-cols-3",
        changed > 0 ? "xl:grid-cols-6" : "xl:grid-cols-5",
      )}
    >
      <Kpi
        label={`Billed spend · ${monthName(data.spend.period)}`}
        icon={<Receipt className="size-3.5" aria-hidden />}
        testId="kpi-spend"
        value={total}
        sub={
          <div>
            <div className="flex h-1.5 overflow-hidden rounded-full bg-zinc-800" aria-hidden>
              <div className="bg-orange-400/80" style={{ width: `${(aws / total) * 100}%` }} />
              <div className="bg-sky-400/80" style={{ width: `${(azure / total) * 100}%` }} />
            </div>
            <p className="mt-1.5">
              <span className="text-orange-300">AWS</span> {formatUsd(aws, { whole: true })} ·{" "}
              <span className="text-sky-300">Azure</span> {formatUsd(azure, { whole: true })}
            </p>
          </div>
        }
      >
        {formatUsd(total)}
      </Kpi>
      <Kpi
        label="Identified waste"
        icon={<Activity className="size-3.5" aria-hidden />}
        testId="kpi-waste"
        value={data.waste.monthly_usd}
        sub={`${formatInt(data.waste.findings)} findings across the estate`}
      >
        <AnimatedNumber value={data.waste.monthly_usd} format={formatMonthly} />
      </Kpi>
      <Kpi
        label="Eligible savings"
        icon={<PiggyBank className="size-3.5" aria-hidden />}
        testId="kpi-eligible"
        value={data.waste.eligible_monthly_usd}
        className="border-emerald-500/25 bg-gradient-to-b from-emerald-500/[0.07] to-transparent"
        sub={
          <span className="flex flex-wrap items-center gap-1.5">
            {formatInt(data.waste.eligible)} findings pass every policy check
            {delta < -0.005 && (
              <Badge
                tone="accent"
                icon={<TrendingDown className="size-3" aria-hidden />}
                className="animate-pop"
              >
                {formatUsdDelta(delta)} realized
              </Badge>
            )}
          </span>
        }
      >
        <span className="text-emerald-300">
          <AnimatedNumber value={data.waste.eligible_monthly_usd} format={formatMonthly} />
        </span>
      </Kpi>
      <Kpi
        label="Resources"
        icon={<Boxes className="size-3.5" aria-hidden />}
        testId="kpi-resources"
        value={data.resources.total}
        sub={`${data.resources.accounts} accounts · AWS ${count("aws")} · Azure ${count("azure")}`}
      >
        {formatInt(data.resources.total)}
      </Kpi>
      <Kpi
        label="Data health"
        icon={<Database className="size-3.5" aria-hidden />}
        testId="kpi-health"
        sub={`${formatInt(data.data_health.quarantined)} records quarantined`}
      >
        <span className="flex flex-wrap items-baseline gap-x-2 text-base">
          <span className="text-emerald-300">{pass} pass</span>
          <span className="text-amber-300">{warn} warn</span>
          {fail > 0 && <span className="text-red-300">{fail} fail</span>}
        </span>
      </Kpi>
      {changed > 0 && (
        <Kpi
          label="Changed in this workspace"
          icon={<Sparkles className="size-3.5" aria-hidden />}
          testId="kpi-changed"
          value={changed}
          className="animate-pop border-emerald-500/25"
          sub="Resources changed by countersigned runs; other workspaces are unaffected"
        >
          {formatInt(changed)} {changed === 1 ? "resource" : "resources"}
        </Kpi>
      )}
    </div>
  );
}

function monthName(period: string): string {
  const d = new Date(`${period}-01T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return period;
  return d.toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
}
