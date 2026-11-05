import { describe, expect, it } from "vitest";
import {
  formatDuration,
  formatMonthly,
  formatRatio,
  formatUsd,
  formatUsdDelta,
  humanize,
  shortHash,
} from "./format";

describe("money", () => {
  it("formats dollars with cents and grouping", () => {
    expect(formatUsd(8897.43)).toBe("$8,897.43");
    expect(formatUsd(682)).toBe("$682.00");
    expect(formatUsd(0.005)).toBe("$0.01");
    expect(formatUsd(1234.5, { whole: true })).toBe("$1,235");
  });

  it("renders missing values as a dash and never a negative zero", () => {
    expect(formatUsd(null)).toBe("—");
    expect(formatUsd(Number.NaN)).toBe("—");
    expect(formatUsd(-0.001)).toBe("$0.00");
    expect(formatMonthly(undefined)).toBe("—");
  });

  it("adds the monthly suffix", () => {
    expect(formatMonthly(221.92)).toBe("$221.92/mo");
  });

  it("signs deltas with a true minus", () => {
    expect(formatUsdDelta(-682)).toBe("−$682.00");
    expect(formatUsdDelta(12)).toBe("+$12.00");
    expect(formatUsdDelta(0.001)).toBe("$0.00");
  });
});

describe("other formats", () => {
  it("shortens hashes", () => {
    expect(shortHash("68cf3b007270d3e7")).toBe("68cf3b00");
    expect(shortHash("abc")).toBe("abc");
    expect(shortHash(null)).toBe("—");
  });

  it("formats durations across scales", () => {
    expect(formatDuration(0.06)).toBe("60 µs");
    expect(formatDuration(1.54)).toBe("1.54 ms");
    expect(formatDuration(250)).toBe("250 ms");
    expect(formatDuration(2500)).toBe("2.50 s");
  });

  it("humanizes identifiers", () => {
    expect(humanize("idle_vm")).toBe("Idle VM");
    expect(humanize("unused_ip")).toBe("Unused IP");
    expect(humanize("task_completion")).toBe("Task completion");
    expect(formatRatio(0.875)).toBe("87.5%");
  });
});
