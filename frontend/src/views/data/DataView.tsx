import { CheckCircle2, Database, ShieldQuestion, TriangleAlert, XCircle } from "lucide-react";
import { Badge, Card, cx, EmptyState, Mono, Skeleton, td, th } from "../../components/ui";
import { useSources } from "../../hooks";
import { formatDateTime, formatInt, humanize, shortHash } from "../../lib/format";

function StatusChip({ status }: { status: string }) {
  if (status === "pass")
    return (
      <Badge tone="accent" icon={<CheckCircle2 className="size-3" aria-hidden />}>
        pass
      </Badge>
    );
  if (status === "warn")
    return (
      <Badge tone="warn" icon={<TriangleAlert className="size-3" aria-hidden />}>
        warn
      </Badge>
    );
  return (
    <Badge tone="danger" icon={<XCircle className="size-3" aria-hidden />}>
      {status}
    </Badge>
  );
}

export default function DataSourcesView() {
  const q = useSources();
  if (q.isPending) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-40" />
        <Skeleton className="h-72" />
      </div>
    );
  }
  if (!q.data) return <EmptyState title="The sources could not be loaded" />;
  const { snapshots, checks, quarantine } = q.data;
  return (
    <div className="space-y-6" data-testid="data-view">
      <Card
        title="Source snapshots"
        icon={<Database className="size-4" aria-hidden />}
        subtitle="Every ingest records each source's checksum, so a changed fixture or export is always visible"
      >
        <div className="grid gap-3 md:grid-cols-3">
          {snapshots.map((s) => (
            <div key={s.source} className="rounded-lg border border-zinc-800 bg-zinc-950/40 p-4">
              <div className="flex items-center justify-between">
                <p className="text-sm font-semibold text-zinc-100">{humanize(s.source)}</p>
                <Mono className="text-[11px] text-zinc-500" title={s.checksum}>
                  sha256 {shortHash(s.checksum, 12)}
                </Mono>
              </div>
              <dl className="mt-3 grid grid-cols-3 gap-2 text-center">
                <div>
                  <dt className="text-[11px] text-zinc-500">records</dt>
                  <dd className="font-mono text-lg text-zinc-100">{formatInt(s.records_read)}</dd>
                </div>
                <div>
                  <dt className="text-[11px] text-zinc-500">quarantined</dt>
                  <dd className={cx("font-mono text-lg", s.quarantined ? "text-amber-300" : "text-zinc-100")}>
                    {s.quarantined}
                  </dd>
                </div>
                <div>
                  <dt className="text-[11px] text-zinc-500">warnings</dt>
                  <dd className={cx("font-mono text-lg", s.warnings ? "text-amber-300" : "text-zinc-100")}>
                    {s.warnings}
                  </dd>
                </div>
              </dl>
              <p className="mt-3 text-xs text-zinc-500">
                fetched {formatDateTime(s.fetched_at)} · data as of {formatDateTime(s.data_as_of)}
              </p>
            </div>
          ))}
        </div>
      </Card>

      <Card
        title="Quality checks"
        icon={<CheckCircle2 className="size-4" aria-hidden />}
        subtitle={`${checks.filter((c) => c.status === "pass").length} pass · ${checks.filter((c) => c.status === "warn").length} warn · ${checks.filter((c) => c.status !== "pass" && c.status !== "warn").length} fail`}
      >
        <div className="scroll-thin -mx-5 overflow-x-auto px-5">
          <table className="w-full min-w-[760px] border-separate border-spacing-0" data-testid="checks-table">
            <thead>
              <tr className="[&>th]:border-b [&>th]:border-zinc-800">
                <th className={th}>Check</th>
                <th className={th}>Category</th>
                <th className={th}>Status</th>
                <th className={th}>Observed</th>
                <th className={th}>Expected</th>
                <th className={th}>Detail</th>
              </tr>
            </thead>
            <tbody>
              {checks.map((c) => (
                <tr key={c.check_id} className="[&>td]:border-b [&>td]:border-zinc-800/70">
                  <td className={td}>
                    <Mono>{c.check_id}</Mono>
                  </td>
                  <td className={td}>{c.category}</td>
                  <td className={td}>
                    <StatusChip status={c.status} />
                  </td>
                  <td className={cx(td, "text-zinc-300")}>{c.observed}</td>
                  <td className={cx(td, "font-mono text-xs text-zinc-400")}>{c.expected}</td>
                  <td className={cx(td, "text-xs text-zinc-400")}>{c.detail || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card
        title="Quarantine"
        icon={<ShieldQuestion className="size-4" aria-hidden />}
        subtitle="Records that failed validation are kept out of the estate, with the reason and the raw input"
      >
        {quarantine.length === 0 ? (
          <EmptyState title="Nothing quarantined" />
        ) : (
          <div className="scroll-thin -mx-5 overflow-x-auto px-5">
            <table
              className="w-full min-w-[760px] border-separate border-spacing-0"
              data-testid="quarantine-table"
            >
              <thead>
                <tr className="[&>th]:border-b [&>th]:border-zinc-800">
                  <th className={th}>Source</th>
                  <th className={th}>Reason</th>
                  <th className={th}>Raw record</th>
                </tr>
              </thead>
              <tbody>
                {quarantine.map((r) => (
                  <tr key={r.source_ref} className="[&>td]:border-b [&>td]:border-zinc-800/70">
                    <td className={cx(td, "max-w-[260px]")}>
                      <Badge>{r.source}</Badge>
                      <span className="mt-1 block break-all font-mono text-[11px] text-zinc-400">
                        {r.source_ref}
                      </span>
                    </td>
                    <td className={cx(td, "max-w-[280px] text-amber-200")}>{r.reason}</td>
                    <td className={td}>
                      <pre className="scroll-thin max-h-28 max-w-[520px] overflow-auto whitespace-pre-wrap break-all rounded-md bg-zinc-950/70 p-2 font-mono text-[11px] text-zinc-400">
                        {r.raw}
                      </pre>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
