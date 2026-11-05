import type { Span } from "./types";

export type SpanOp = "invoke_agent" | "chat" | "execute_tool" | "execute_action" | "other";

export interface WaterfallRow {
  span: Span;
  depth: number;
  op: SpanOp;
  /** Milliseconds from the first span's start. */
  startMs: number;
  durationMs: number;
  offsetPct: number;
  widthPct: number;
}

export interface Waterfall {
  rows: WaterfallRow[];
  /** Real wall-clock length of the trace. */
  totalMs: number;
  /** How many idle gaps were collapsed (0 on the real clock). */
  collapsedGaps: number;
}

export function spanOp(span: Span): SpanOp {
  const attr = span.attributes?.["gen_ai.operation.name"];
  const name = typeof attr === "string" ? attr : span.name.split(" ")[0];
  if (name === "invoke_agent" || name === "chat" || name === "execute_tool" || name === "execute_action")
    return name;
  return "other";
}

/**
 * A time mapping that shrinks every idle gap (no span open) to `gapNs`. Runs wait between ticks and for
 * humans, so on a real clock the work is a few slivers; collapsing the gaps keeps every span readable.
 */
export function collapseGaps(spans: Span[], share = 0.25): { map: (t: number) => number; gaps: number } {
  const intervals = spans
    .map((s) => [s.start_ns, Math.max(s.end_ns, s.start_ns)] as [number, number])
    .sort((a, b) => a[0] - b[0]);
  const merged: Array<[number, number]> = [];
  for (const [start, end] of intervals) {
    const last = merged[merged.length - 1];
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }
  const active = merged.reduce((sum, [a, b]) => sum + (b - a), 0);
  const rawGaps = merged.slice(1).map((m, i) => m[0] - (merged[i]?.[1] ?? m[0]));
  const gapNs = Math.max((Math.max(active, 1) * share) / Math.max(rawGaps.length, 1), 1);
  const cuts: Array<{ at: number; removed: number }> = [];
  let removed = 0;
  merged.slice(1).forEach((m, i) => {
    const gap = rawGaps[i] ?? 0;
    if (gap > gapNs) {
      removed += gap - gapNs;
      cuts.push({ at: m[0], removed });
    }
  });
  const map = (t: number) => {
    let r = 0;
    for (const c of cuts) {
      if (t >= c.at) r = c.removed;
      else break;
    }
    return t - r;
  };
  return { map, gaps: cuts.length };
}

/**
 * Lay out spans as a waterfall: children under their parent (depth-first, by start time), bars positioned
 * relative to the first span's start. A span whose parent is not in the list is a root. Bars never shrink
 * below `minWidthPct`, so instant spans stay visible. With `collapse`, idle gaps are shrunk (see above);
 * `startMs` and `durationMs` always stay on the real clock.
 */
export function layoutWaterfall(
  spans: Span[],
  opts: { minWidthPct?: number; collapse?: boolean } = {},
): Waterfall {
  const minWidthPct = opts.minWidthPct ?? 0.4;
  if (spans.length === 0) return { rows: [], totalMs: 0, collapsedGaps: 0 };
  const ids = new Set(spans.map((s) => s.span_id));
  const children = new Map<string, Span[]>();
  const roots: Span[] = [];
  for (const s of spans) {
    if (s.parent_id && ids.has(s.parent_id) && s.parent_id !== s.span_id) {
      const list = children.get(s.parent_id) ?? [];
      list.push(s);
      children.set(s.parent_id, list);
    } else {
      roots.push(s);
    }
  }
  const byStart = (a: Span, b: Span) => a.start_ns - b.start_ns || a.end_ns - b.end_ns;
  const compressed = opts.collapse ? collapseGaps(spans) : null;
  const axis = compressed?.map ?? ((t: number) => t);
  const t0 = Math.min(...spans.map((s) => s.start_ns));
  const t1 = Math.max(...spans.map((s) => Math.max(s.end_ns, s.start_ns)));
  const a0 = axis(t0);
  const axisNs = Math.max(axis(t1) - a0, 1);
  const rows: WaterfallRow[] = [];
  const seen = new Set<string>();
  const visit = (span: Span, depth: number) => {
    if (seen.has(span.span_id)) return;
    seen.add(span.span_id);
    const end = Math.max(span.end_ns, span.start_ns);
    const offsetPct = Math.min(((axis(span.start_ns) - a0) / axisNs) * 100, 100);
    const rawWidth = ((axis(end) - axis(span.start_ns)) / axisNs) * 100;
    const widthPct = Math.min(Math.max(rawWidth, minWidthPct), Math.max(100 - offsetPct, minWidthPct));
    rows.push({
      span,
      depth,
      op: spanOp(span),
      startMs: (span.start_ns - t0) / 1e6,
      durationMs: (end - span.start_ns) / 1e6,
      offsetPct,
      widthPct,
    });
    for (const child of (children.get(span.span_id) ?? []).sort(byStart)) visit(child, depth + 1);
  };
  for (const root of roots.sort(byStart)) visit(root, 0);
  return { rows, totalMs: (t1 - t0) / 1e6, collapsedGaps: compressed?.gaps ?? 0 };
}

/** The gen_ai.* (and countersign.*) attributes of a span, for the hover card. */
export function spanAttributes(span: Span): Array<[string, string]> {
  return Object.entries(span.attributes ?? {})
    .filter(([k]) => k.startsWith("gen_ai.") || k.startsWith("countersign.") || k === "error.type")
    .map(([k, v]) => [k, Array.isArray(v) ? v.join(", ") : String(v)] as [string, string]);
}
