import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, BarChart3, Boxes, Filter, ShieldAlert } from "lucide-react";
import { useCallback, useId, useState } from "react";
import { Drawer } from "../../components/Drawer";
import { Badge, Card, cx, EmptyState, Kv, Mono, Skeleton, td, th } from "../../components/ui";
import { useDashboard } from "../../hooks";
import { formatDateTime, formatMonthly, formatPercent, humanize, providerLabel } from "../../lib/format";
import type { Finding, ResourceDetail } from "../../lib/types";
import { useUrlParam } from "../../lib/url";
import { useSession } from "../../session";
import { envTone, ProviderBadge } from "../agent/ChangeRequest";
import { CostChart, CpuChart, SpendChart, WasteChart } from "./charts";

interface Filters {
  category: string;
  environment: string;
  provider: string;
  eligible: boolean;
}

function useFindings(f: Filters) {
  const { api, workspace } = useSession();
  const qs = new URLSearchParams();
  if (f.category) qs.set("category", f.category);
  if (f.environment) qs.set("environment", f.environment);
  if (f.provider) qs.set("provider", f.provider);
  if (f.eligible) qs.set("eligible", "true");
  return useQuery({
    queryKey: ["findings", workspace.workspace_id, qs.toString()],
    queryFn: () => api<{ count: number; findings: Finding[] }>(`/api/findings${qs.size ? `?${qs}` : ""}`),
    placeholderData: (prev) => prev,
  });
}

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: Array<[string, string]>;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-[11px] uppercase tracking-wide text-zinc-500">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 rounded-lg border border-zinc-700 bg-zinc-900 px-2 text-sm text-zinc-200 focus:border-emerald-500/60 focus:outline-none"
      >
        <option value="">All</option>
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </div>
  );
}

export default function EstateView() {
  const dashboard = useDashboard();
  const [filters, setFilters] = useState<Filters>({
    category: "",
    environment: "",
    provider: "",
    eligible: false,
  });
  const findings = useFindings(filters);
  const [selected, setSelected] = useUrlParam("resource");
  const close = useCallback(() => setSelected(null, "replace"), [setSelected]);
  const eligibleId = useId();
  const rows = [...(findings.data?.findings ?? [])].sort(
    (a, b) => (b.est_monthly_savings_usd ?? 0) - (a.est_monthly_savings_usd ?? 0),
  );
  const categories = Object.keys(dashboard.data?.waste.by_category ?? {}).map(
    (k) => [k, humanize(k)] as [string, string],
  );
  const environments = Object.keys(dashboard.data?.resources.by_environment ?? {}).map(
    (k) => [k, k] as [string, string],
  );

  return (
    <div className="space-y-6" data-testid="estate-view">
      <div className="grid gap-6 lg:grid-cols-2">
        <Card
          title={`Daily billed spend · ${dashboard.data?.spend.period ?? "October"}`}
          icon={<BarChart3 className="size-4" aria-hidden />}
          subtitle="FOCUS billing exports from both providers, stacked per day"
        >
          {dashboard.data ? <SpendChart daily={dashboard.data.spend.daily} /> : <Skeleton className="h-64" />}
        </Card>
        <Card
          title="Waste by category"
          icon={<BarChart3 className="size-4" aria-hidden />}
          subtitle="Estimated per month; grey is blocked by policy (production, protected, insufficient data)"
        >
          {dashboard.data ? (
            <WasteChart byCategory={dashboard.data.waste.by_category} />
          ) : (
            <Skeleton className="h-64" />
          )}
        </Card>
      </div>

      <Card
        title="Findings"
        icon={<Boxes className="size-4" aria-hidden />}
        subtitle={
          findings.data
            ? `${findings.data.count} findings, sorted by estimated savings. Select a row for the resource's metrics and costs.`
            : "Loading findings…"
        }
        actions={
          <div className="flex flex-wrap items-end gap-3">
            <Filter className="mb-2 size-4 text-zinc-500" aria-hidden />
            <Select
              label="Category"
              value={filters.category}
              options={categories}
              onChange={(v) => setFilters((f) => ({ ...f, category: v }))}
            />
            <Select
              label="Environment"
              value={filters.environment}
              options={environments}
              onChange={(v) => setFilters((f) => ({ ...f, environment: v }))}
            />
            <Select
              label="Provider"
              value={filters.provider}
              options={[
                ["aws", "AWS"],
                ["azure", "Azure"],
              ]}
              onChange={(v) => setFilters((f) => ({ ...f, provider: v }))}
            />
            <label
              htmlFor={eligibleId}
              className="mb-1.5 inline-flex items-center gap-2 text-sm text-zinc-300"
            >
              <input
                id={eligibleId}
                type="checkbox"
                checked={filters.eligible}
                onChange={(e) => setFilters((f) => ({ ...f, eligible: e.target.checked }))}
                className="size-4 rounded border-zinc-600 bg-zinc-900 accent-emerald-500"
              />
              Eligible only
            </label>
          </div>
        }
      >
        {findings.isPending ? (
          <div className="space-y-2">
            {["a", "b", "c", "d", "e"].map((k) => (
              <Skeleton key={k} className="h-10" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState title="No findings match these filters">
            Clear a filter to see more of the estate.
          </EmptyState>
        ) : (
          <div
            className={cx(
              "scroll-thin -mx-5 overflow-x-auto px-5 transition-opacity",
              findings.isFetching && "opacity-60",
            )}
          >
            <table
              className="w-full min-w-[900px] border-separate border-spacing-0"
              data-testid="findings-table"
            >
              <caption className="sr-only">Findings</caption>
              <thead>
                <tr className="[&>th]:border-b [&>th]:border-zinc-800">
                  <th className={th}>Resource</th>
                  <th className={th}>Category</th>
                  <th className={th}>Operation</th>
                  <th className={cx(th, "text-right")}>Savings</th>
                  <th className={th}>Status</th>
                  <th className={th}>Blockers and warnings</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((f) => (
                  <tr
                    key={f.ref}
                    className="cursor-pointer [&>td]:border-b [&>td]:border-zinc-800/70 hover:bg-zinc-800/25"
                    onClick={() => setSelected(f.ref)}
                    data-testid="finding-row"
                  >
                    <td className={td}>
                      <button
                        type="button"
                        className="text-left"
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelected(f.ref);
                        }}
                        aria-label={`Open ${f.ref}`}
                      >
                        <Mono className="text-zinc-100">{f.ref}</Mono>
                        {f.name !== f.ref && (
                          <span className="block max-w-[260px] truncate text-xs text-zinc-400" title={f.name}>
                            {f.name}
                          </span>
                        )}
                      </button>
                      <div className="mt-1 flex flex-wrap gap-1">
                        <ProviderBadge provider={f.provider} />
                        <Badge tone={envTone(f.environment)}>{f.environment}</Badge>
                        {f.flagged_untrusted_text && (
                          <Badge tone="danger" icon={<ShieldAlert className="size-3" aria-hidden />}>
                            instruction-like text
                          </Badge>
                        )}
                      </div>
                    </td>
                    <td className={td}>{humanize(f.category)}</td>
                    <td className={td}>
                      {f.operation ? (
                        humanize(f.operation)
                      ) : (
                        <span className="text-zinc-500">report only</span>
                      )}
                      {f.target_sku && (
                        <span className="block font-mono text-[11px] text-zinc-400">
                          {f.current_sku} → {f.target_sku}
                        </span>
                      )}
                    </td>
                    <td
                      className={cx(
                        td,
                        "text-right font-mono tabular-nums",
                        f.eligible ? "text-emerald-300" : "text-zinc-400",
                      )}
                    >
                      {formatMonthly(f.est_monthly_savings_usd)}
                    </td>
                    <td className={td}>
                      {f.eligible ? <Badge tone="accent">eligible</Badge> : <Badge>blocked</Badge>}
                    </td>
                    <td className={td}>
                      <div className="flex flex-wrap gap-1">
                        {f.blockers.map((b) => (
                          <Badge key={b} tone="danger">
                            {b}
                          </Badge>
                        ))}
                        {f.warnings.map((w) => (
                          <Badge key={w} tone="warn" icon={<AlertTriangle className="size-3" aria-hidden />}>
                            {w}
                          </Badge>
                        ))}
                        {f.blockers.length === 0 && f.warnings.length === 0 && (
                          <span className="text-xs text-zinc-500">none</span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <ResourceDrawer refId={selected} onClose={close} />
    </div>
  );
}

function ResourceDrawer({ refId, onClose }: { refId: string | null; onClose: () => void }) {
  const { api, workspace } = useSession();
  const q = useQuery({
    queryKey: ["resource", workspace.workspace_id, refId],
    enabled: Boolean(refId),
    queryFn: () => api<ResourceDetail>(`/api/resources/${encodeURIComponent(refId ?? "")}`),
  });
  const d = q.data;
  const r = d?.resource;
  const flagged = Boolean(r?.flags.includes("suspicious_content") || d?.finding?.flagged_untrusted_text);
  return (
    <Drawer
      open={Boolean(refId)}
      onClose={onClose}
      title={<span className="font-mono">{refId}</span>}
      subtitle={
        r ? `${providerLabel(r.provider)} · ${r.kind} · ${r.region} · account ${r.account}` : "Loading…"
      }
    >
      {q.isPending ? (
        <div className="space-y-3">
          <Skeleton className="h-5 w-1/2" />
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
        </div>
      ) : !d || !r ? (
        <EmptyState title="Resource not found" />
      ) : (
        <div className="space-y-6" data-testid="resource-drawer">
          {flagged && (
            <div
              className="flex gap-3 rounded-lg border border-red-500/40 bg-red-500/10 px-3.5 py-3 text-sm text-red-100"
              role="note"
            >
              <ShieldAlert className="mt-0.5 size-4 shrink-0 text-red-300" aria-hidden />
              <p>
                This resource's metadata contains instruction-like text. It is displayed here escaped, for
                humans only, and is never shown to the model; policy rule P-003 keeps the resource out of
                every change request.
              </p>
            </div>
          )}
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Kv label="State">{r.state}</Kv>
            <Kv label="SKU">{r.sku ?? "—"}</Kv>
            <Kv label="Environment">{r.environment}</Kv>
            <Kv label="Team">{r.team ?? "—"}</Kv>
            <Kv label="Owner">{r.owner ?? "no owner tag"}</Kv>
            <Kv label="Created">{formatDateTime(r.created_at)}</Kv>
            {r.size_gb !== null && <Kv label="Size">{r.size_gb} GB</Kv>}
            {r.attached_to && <Kv label="Attached to">{r.attached_to}</Kv>}
            {r.detached_at && <Kv label="Detached">{formatDateTime(r.detached_at)}</Kv>}
          </dl>
          <div>
            <p className="text-[11px] uppercase tracking-wide text-zinc-500">Resource id</p>
            <p className="mt-0.5 break-all font-mono text-xs text-zinc-300">{r.id}</p>
            <p className="mt-1 break-all font-mono text-[11px] text-zinc-500">source {r.source_ref}</p>
          </div>

          {d.finding && (
            <section aria-label="Finding" className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-zinc-100">{humanize(d.finding.category)}</span>
                {d.finding.eligible ? <Badge tone="accent">eligible</Badge> : <Badge>blocked</Badge>}
                <span className="ml-auto font-mono text-sm text-emerald-300">
                  {formatMonthly(d.finding.est_monthly_savings_usd)}
                </span>
              </div>
              {d.finding.utilization && (
                <p className="mt-1.5 text-xs text-zinc-400">
                  p95 daily average {formatPercent(d.finding.utilization.p95_daily_avg, 2)} · peak{" "}
                  {formatPercent(d.finding.utilization.max, 2)} · coverage{" "}
                  {Math.round(d.finding.utilization.coverage * 100)}% ({d.finding.utilization.datapoints}/
                  {d.finding.utilization.expected_days} days)
                </p>
              )}
              {(d.finding.blockers.length > 0 || d.finding.warnings.length > 0) && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {d.finding.blockers.map((b) => (
                    <Badge key={b} tone="danger">
                      {b}
                    </Badge>
                  ))}
                  {d.finding.warnings.map((w) => (
                    <Badge key={w} tone="warn">
                      {w}
                    </Badge>
                  ))}
                </div>
              )}
              {d.finding.price_reference && (
                <p className="mt-2 break-all text-[11px] text-zinc-500">
                  price ({d.finding.price_source}): {d.finding.price_reference}
                </p>
              )}
            </section>
          )}

          <section aria-label="Tags">
            <h3 className="text-sm font-medium text-zinc-200">Tags</h3>
            <table className="mt-2 w-full text-sm">
              <tbody>
                {Object.entries(r.tags).map(([k, v]) => (
                  <tr key={k} className="border-b border-zinc-800/70 align-top">
                    <td className="w-1/3 py-1.5 pr-3 font-mono text-xs text-zinc-400">{k}</td>
                    <td
                      className={cx(
                        "break-words py-1.5 font-mono text-xs",
                        flagged ? "text-red-200" : "text-zinc-200",
                      )}
                    >
                      {v}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {d.cpu.length > 0 && (
            <section aria-label="CPU utilization">
              <h3 className="text-sm font-medium text-zinc-200">CPU utilization · {d.cpu.length} days</h3>
              <CpuChart cpu={d.cpu} />
            </section>
          )}
          {d.daily_cost.length > 0 && (
            <section aria-label="Daily cost">
              <h3 className="text-sm font-medium text-zinc-200">Daily billed cost</h3>
              <CostChart cost={d.daily_cost} />
            </section>
          )}
        </div>
      )}
    </Drawer>
  );
}
