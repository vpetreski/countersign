import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import published from "../../../../backend/countersign/evals/published/latest.json";
import type { EvalReport } from "../../lib/types";
import { criteriaNames, EvalReportView } from "./EvalsView";

describe("EvalReportView", () => {
  const report = published as unknown as EvalReport;

  it("renders the published report: honesty, criteria with their method, the mutation matrix", () => {
    render(<EvalReportView report={report} isSample={false} />);
    expect(screen.getByTestId("honesty")).toHaveTextContent(/not a language model/);
    expect(screen.getAllByTestId("criterion-row")).toHaveLength(6);
    expect(screen.getAllByText(/wilson over cases/).length).toBeGreaterThan(0);
    const matrix = screen.getByTestId("mutation-matrix");
    const rows = within(matrix).getAllByTestId("mutant-row");
    expect(rows).toHaveLength(Object.keys(report.mutation?.mutants ?? {}).length);
    // Every mutant has exactly one targeted cell, and the quitter's target counts its discordant cases.
    expect(matrix.querySelectorAll("[data-expected]")).toHaveLength(rows.length);
    const quitter = rows.find((r) => r.textContent?.includes("quitter"));
    expect(quitter?.querySelector("[data-expected]")).toHaveTextContent(
      String(report.mutation?.mutants?.quitter?.discordant_cases?.task_completion),
    );
    expect(screen.getByTestId("cases-table")).toHaveTextContent("c01-default-dev-vms-and-disks");
  });

  it("states a calibration miss plainly, with the realized slip rate and z", () => {
    const missed: EvalReport = {
      ...report,
      calibration: {
        ...report.calibration,
        covered: false,
        realized: { slips: 28, chances: 200, rate: 0.14, z: -2.121 },
      },
    };
    render(<EvalReportView report={missed} isSample={false} />);
    const miss = screen.getByTestId("calibration-miss");
    expect(miss).toHaveTextContent("one draw in twenty");
    expect(miss).toHaveTextContent("z = -2.12");
    expect(miss).not.toHaveAttribute("role", "alert");
  });

  it("renders a sparse report without crashing", () => {
    render(<EvalReportView report={{ criteria: { safety: { mean: 1 } }, cases: [{ id: "c1" }] }} isSample />);
    expect(screen.getByText("Sample data")).toBeInTheDocument();
    expect(screen.getAllByTestId("criterion-row")).toHaveLength(1);
    expect(screen.getByText("No mutants in this report")).toBeInTheDocument();
  });

  it("orders the known criteria first", () => {
    expect(criteriaNames({ criteria: { zeta: {}, safety: {}, task_completion: {} } })).toEqual([
      "task_completion",
      "safety",
      "zeta",
    ]);
  });
});
