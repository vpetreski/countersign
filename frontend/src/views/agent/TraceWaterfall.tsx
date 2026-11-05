import { Waypoints } from "lucide-react";
import { useState } from "react";
import { cx, EmptyState, Mono, Skeleton } from "../../components/ui";
import { useTrace } from "../../hooks";
import { formatDuration } from "../../lib/format";
import { layoutWaterfall, type SpanOp, spanAttributes, type WaterfallRow } from "../../lib/trace";

export const OP_STYLE: Record<SpanOp, { bar: string; label: string }> = {
  invoke_agent: { bar: "bg-violet-400/80", label: "invoke_agent" },
  chat: { bar: "bg-sky-400/80", label: "chat" },
  execute_tool: { bar: "bg-fuchsia-400/80", label: "execute_tool" },
  execute_action: { bar: "bg-emerald-400/80", label: "execute_action" },
  other: { bar: "bg-zinc-400/70", label: "other" },
};

export function TraceWaterfall({ runId, version }: { runId: string; version: number }) {
  const trace = useTrace(runId, version, true);
  const [hovered, setHovered] = useState<string | null>(null);
  const [realClock, setRealClock] = useState(false);
  if (trace.isPending) {
    return (
      <div className="space-y-2" aria-busy="true">
        {["a", "b", "c", "d", "e", "f"].map((k) => (
          <Skeleton key={k} className="h-5" />
        ))}
      </div>
    );
  }
  const spans = trace.data?.spans ?? [];
  if (spans.length === 0) {
    return (
      <EmptyState icon={<Waypoints className="size-6" />} title="No spans yet">
        Spans appear after the first tick of the run.
      </EmptyState>
    );
  }
  const { rows, totalMs, collapsedGaps } = layoutWaterfall(spans, { collapse: !realClock });
  const selected =
    rows.find((r) => r.span.span_id === hovered) ?? rows.find((r) => r.op === "chat") ?? rows[0];
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  return (
    <div data-testid="trace">
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-400">
        <span>
          trace <Mono className="text-[11px]">{trace.data?.trace_id}</Mono>
        </span>
        <span>{rows.length} spans</span>
        <span>{formatDuration(totalMs)} wall clock</span>
        <button
          type="button"
          onClick={() => setRealClock((v) => !v)}
          aria-pressed={realClock}
          className="rounded-md border border-zinc-700 px-2 py-0.5 text-[11px] text-zinc-300 hover:border-zinc-500"
        >
          {realClock ? "Real clock" : `Idle gaps collapsed (${collapsedGaps})`}
        </button>
        <span className="ml-auto flex flex-wrap gap-3">
          {(Object.keys(OP_STYLE) as SpanOp[])
            .filter((op) => op !== "other")
            .map((op) => (
              <span key={op} className="inline-flex items-center gap-1.5">
                <span aria-hidden className={cx("size-2 rounded-sm", OP_STYLE[op].bar)} />
                <span className="font-mono text-[11px]">{OP_STYLE[op].label}</span>
              </span>
            ))}
        </span>
      </div>
      <div className="scroll-thin overflow-x-auto">
        <div className="min-w-[720px]">
          <div className="grid grid-cols-[minmax(220px,32%)_1fr] border-b border-zinc-800 pb-1 text-[10px] text-zinc-500">
            <span>Span</span>
            <span className="relative h-3">
              {realClock ? (
                ticks.map((t) => (
                  <span
                    key={t}
                    className="absolute -translate-x-1/2 font-mono first:translate-x-0 last:-translate-x-full"
                    style={{ left: `${t * 100}%` }}
                  >
                    {formatDuration(totalMs * t)}
                  </span>
                ))
              ) : (
                <span className="absolute inset-x-0 text-center">
                  time on a compressed axis: waits between ticks and for humans are shrunk
                </span>
              )}
            </span>
          </div>
          <ul className="scroll-thin max-h-[520px] overflow-y-auto" aria-label="Trace spans">
            {rows.map((r) => (
              <li
                key={r.span.span_id}
                className={cx(
                  "grid grid-cols-[minmax(220px,32%)_1fr] items-center border-b border-zinc-900 py-1",
                  selected?.span.span_id === r.span.span_id ? "bg-zinc-800/50" : "hover:bg-zinc-800/30",
                )}
                data-op={r.op}
                onMouseEnter={() => setHovered(r.span.span_id)}
              >
                <span
                  className="flex min-w-0 items-center gap-1.5 pr-3"
                  style={{ paddingLeft: `${r.depth * 14}px` }}
                >
                  <span aria-hidden className={cx("size-1.5 shrink-0 rounded-full", OP_STYLE[r.op].bar)} />
                  <span className="truncate font-mono text-[11px] text-zinc-300" title={r.span.name}>
                    {r.span.name}
                  </span>
                </span>
                <span className="relative h-5">
                  {ticks.slice(1, -1).map((t) => (
                    <span
                      key={t}
                      aria-hidden
                      className="absolute inset-y-0 w-px bg-zinc-800/60"
                      style={{ left: `${t * 100}%` }}
                    />
                  ))}
                  <button
                    type="button"
                    onFocus={() => setHovered(r.span.span_id)}
                    onClick={() => setHovered(r.span.span_id)}
                    aria-label={`${r.span.name}: starts at ${formatDuration(r.startMs)}, lasts ${formatDuration(r.durationMs)}`}
                    className={cx(
                      "absolute inset-y-1 rounded-sm opacity-85 transition-opacity hover:opacity-100",
                      OP_STYLE[r.op].bar,
                    )}
                    style={{ left: `${r.offsetPct}%`, width: `${r.widthPct}%` }}
                  />
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <SpanDetails row={selected} />
    </div>
  );
}

function SpanDetails({ row }: { row: WaterfallRow | undefined }) {
  if (!row) return null;
  const attrs = spanAttributes(row.span);
  return (
    <div
      className="mt-3 rounded-lg border border-zinc-800 bg-zinc-950/50 p-3"
      aria-live="polite"
      data-testid="span-details"
    >
      <p className="flex flex-wrap items-center gap-2 text-xs">
        <span aria-hidden className={cx("size-2 rounded-sm", OP_STYLE[row.op].bar)} />
        <Mono className="text-zinc-100">{row.span.name}</Mono>
        <span className="text-zinc-500">
          +{formatDuration(row.startMs)} · {formatDuration(row.durationMs)} · {row.span.kind} · status{" "}
          {row.span.status}
        </span>
      </p>
      <dl className="mt-2 grid gap-x-6 gap-y-1 sm:grid-cols-2">
        {attrs.map(([k, v]) => (
          <div key={k} className="flex min-w-0 gap-2 font-mono text-[11px]">
            <dt className="shrink-0 text-zinc-500">{k}</dt>
            <dd className="truncate text-zinc-200" title={v}>
              {v}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
