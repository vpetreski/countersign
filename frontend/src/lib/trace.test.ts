import { describe, expect, it } from "vitest";
import { collapseGaps, layoutWaterfall, spanAttributes, spanOp } from "./trace";
import type { Span } from "./types";

const ms = 1_000_000;
const span = (id: string, parent: string | null, start: number, end: number, op: string): Span => ({
  trace_id: "t",
  span_id: id,
  parent_id: parent,
  name: `${op} thing`,
  kind: "internal",
  start_ns: 1_793_849_401_000_000_000 + start * ms,
  end_ns: 1_793_849_401_000_000_000 + end * ms,
  status: "unset",
  attributes:
    op === "execute_action" ? { "countersign.action.id": "act-01" } : { "gen_ai.operation.name": op },
});

describe("layoutWaterfall", () => {
  const spans = [
    span("c1", "a", 10, 30, "chat"),
    span("a", "missing-root", 0, 100, "invoke_agent"),
    span("t1", "a", 40, 60, "execute_tool"),
    span("x", "b", 210, 220, "execute_action"),
    span("b", "missing-root", 200, 250, "invoke_agent"),
  ];

  it("nests children under parents, ordered by start; unknown parents become roots", () => {
    const { rows } = layoutWaterfall(spans);
    expect(rows.map((r) => [r.span.span_id, r.depth])).toEqual([
      ["a", 0],
      ["c1", 1],
      ["t1", 1],
      ["b", 0],
      ["x", 1],
    ]);
  });

  it("positions bars relative to the first span on the real clock", () => {
    const { rows, totalMs } = layoutWaterfall(spans);
    // Epoch nanoseconds exceed 2^53, so JSON numbers carry about 256 ns of rounding.
    expect(totalMs).toBeCloseTo(250, 3);
    const b = rows.find((r) => r.span.span_id === "b");
    expect(b?.offsetPct).toBeCloseTo(80, 3);
    expect(b?.widthPct).toBeCloseTo(20, 3);
    expect(b?.startMs).toBeCloseTo(200, 3);
    expect(b?.durationMs).toBeCloseTo(50, 3);
  });

  it("keeps instant spans visible with a minimum width", () => {
    const { rows } = layoutWaterfall([
      span("a", null, 0, 1000, "invoke_agent"),
      span("z", "a", 500, 500, "chat"),
    ]);
    expect(rows[1]?.widthPct).toBe(0.4);
  });

  it("collapses idle gaps but keeps real timings", () => {
    const real = layoutWaterfall(spans);
    const packed = layoutWaterfall(spans, { collapse: true });
    expect(packed.collapsedGaps).toBe(1);
    const b = packed.rows.find((r) => r.span.span_id === "b");
    expect(b?.offsetPct).toBeLessThan(real.rows.find((r) => r.span.span_id === "b")?.offsetPct ?? 0);
    expect(b?.startMs).toBeCloseTo(200, 3);
    const { map } = collapseGaps(spans);
    expect(map(spans[1]?.start_ns ?? 0)).toBe(spans[1]?.start_ns);
  });

  it("classifies spans by operation and lists gen_ai attributes", () => {
    expect(spanOp(spans[0] as Span)).toBe("chat");
    expect(spanOp(spans[3] as Span)).toBe("execute_action");
    expect(spanAttributes(spans[0] as Span)).toEqual([["gen_ai.operation.name", "chat"]]);
    expect(layoutWaterfall([])).toEqual({ rows: [], totalMs: 0, collapsedGaps: 0 });
  });
});
