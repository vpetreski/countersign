import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Evidence } from "../lib/types";
import { CitedSummary, CitedText } from "./Citations";

const evidence: Record<string, Evidence> = {
  "ev-01": { id: "ev-01", tool: "get_estate_summary", ref: "", kind: "estate", summary: "149 resources" },
  "ev-02": { id: "ev-02", tool: "find_waste", ref: "i-1", kind: "finding", summary: "idle_vm i-1: $10/mo" },
};

describe("CitedText", () => {
  it("renders each cited id as an interactive chip with its evidence", () => {
    render(<CitedText text="Saves money [ev-01, ev-02] today." evidence={evidence} />);
    const chips = screen.getAllByRole("button");
    expect(chips.map((c) => c.textContent)).toEqual(["ev-01", "ev-02"]);
    expect(chips[1]).toHaveAccessibleName("Evidence ev-02: idle_vm i-1: $10/mo");
    expect(screen.getByText(/Saves money/)).toBeInTheDocument();
    expect(screen.getAllByRole("tooltip", { hidden: true })[0]).toHaveTextContent("get_estate_summary");
  });

  it("marks citations that point at no evidence", () => {
    render(<CitedText text="x [ev-99]" evidence={evidence} />);
    expect(screen.getByRole("button")).toHaveAccessibleName("Evidence ev-99 (not found)");
  });

  it("escapes text instead of interpreting it", () => {
    render(<CitedText text={'<img src=x onerror="alert(1)"> [ev-01]'} evidence={evidence} />);
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByText(/<img src=x/)).toBeInTheDocument();
  });
});

describe("CitedSummary", () => {
  it("lists actions and groups the exclusions", () => {
    render(
      <CitedSummary
        evidence={evidence}
        text="Proposing 1 change [ev-01]. stop_vm i-1: idle. [ev-02] Not proposed: i-5: protected [ev-01]; i-8: too new [ev-02]."
      />,
    );
    expect(screen.getByText("Not proposed")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").length).toBeGreaterThanOrEqual(3);
  });
});
