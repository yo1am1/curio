import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { GroundingReport } from "../../api/types";
import { GroundingBadge } from "./GroundingBadge";
import { describeChecked } from "./grounding";

function report(fields: Partial<GroundingReport>): GroundingReport {
  return { status: "verified", checked: 0, issues: [], counts: {}, ...fields };
}

describe("GroundingBadge", () => {
  it("says what was checked against the catalog", () => {
    render(
      <GroundingBadge
        domain="fashion"
        grounding={report({ checked: 5, counts: { price: 3, name: 2 } })}
      />,
    );
    expect(screen.getByTestId("grounding-badge")).toHaveTextContent(
      "Checked 3 prices and 2 names against the catalog",
    );
  });

  it("names three kinds and sums up the rest", () => {
    const grounding = report({
      checked: 19,
      counts: { id: 5, price: 8, title: 1, rating: 2, name: 2, alcohol: 1 },
    });
    expect(describeChecked(grounding)).toBe("8 prices, 1 title, 2 names and 8 other facts");
    render(<GroundingBadge domain="evening" grounding={grounding} />);
    expect(screen.getByTestId("grounding-badge")).toHaveTextContent("against the catalogs");
  });

  it("counts facts when an older server sends no counts", () => {
    expect(describeChecked(report({ checked: 2, counts: undefined }))).toBe("2 facts");
  });

  it("shows nothing when nothing checkable was found", () => {
    const { container } = render(
      <GroundingBadge domain="books" grounding={report({ status: "not_applicable" })} />,
    );
    expect(container).toBeEmptyDOMElement();
    render(<GroundingBadge domain="books" grounding={report({ checked: 0 })} />);
    expect(screen.queryByTestId("grounding-badge")).toBeNull();
  });

  it("keeps the repaired and fallback labels honest", () => {
    render(
      <GroundingBadge
        domain="cocktails"
        grounding={report({
          status: "fallback",
          checked: 2,
          issues: ["Mojito is not alcohol-free"],
          counts: { alcohol: 1, name: 1 },
        })}
      />,
    );
    const badge = screen.getByTestId("grounding-badge");
    expect(badge).toHaveTextContent("Unverified answer replaced with recipe facts");
    expect(badge.querySelector("[title]")?.getAttribute("title")).toBe(
      "1 claim did not match the tools' results; checked 1 name and 1 alcohol label",
    );
  });
});
