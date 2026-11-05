import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { NotProposedTable } from "./NotProposed";

describe("NotProposedTable", () => {
  it("lists each left-out finding with its reason and evidence chips", () => {
    render(
      <NotProposedTable
        items={[
          { ref: "i-0c4f9e2a7b1d30004", reason: "production: reported only", evidence_ids: ["ev-02"] },
          { ref: "vol-4", reason: "protected by compliance=legal-hold", evidence_ids: [] },
        ]}
        evidence={{
          "ev-02": {
            id: "ev-02",
            tool: "find_waste",
            ref: "i-0c4f9e2a7b1d30004",
            kind: "finding",
            summary: "prod",
          },
        }}
      />,
    );
    const rows = screen.getAllByTestId("not-proposed-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("production: reported only");
    expect(within(rows[0] as HTMLElement).getByRole("button")).toHaveAccessibleName("Evidence ev-02: prod");
    expect(rows[1]).toHaveTextContent("none");
  });

  it("renders nothing when nothing was left out", () => {
    const { container } = render(<NotProposedTable items={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
