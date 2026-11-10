import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { GroundingReport } from "../../api/types";
import { GroundingBadge } from "./GroundingBadge";
import { describeChecked, describeCounts, renderedTotal } from "./grounding";

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

  it("counts the facts the server rendered and the typed ones it checked", () => {
    render(
      <GroundingBadge
        domain="fashion"
        grounding={report({
          checked: 1,
          counts: { price: 1 },
          rendered: { name: 2, price: 2, total: 1 },
        })}
      />,
    );
    const badge = screen.getByTestId("grounding-badge");
    expect(badge).toHaveTextContent(/^5 facts from the catalog · 1 more checked against it$/);
    expect(badge.querySelector("[title]")?.getAttribute("title")).toBe(
      "Rendered by the server from the catalog: 2 prices, 2 names and 1 total. " +
        "Typed by the model and checked: 1 price",
    );
  });

  it("shows rendered facts alone when the model typed nothing checkable", () => {
    render(
      <GroundingBadge
        domain="cocktails"
        grounding={report({ checked: 0, rendered: { name: 1, alcohol: 1, amount: 2 } })}
      />,
    );
    const badge = screen.getByTestId("grounding-badge");
    expect(badge).toHaveTextContent(/^4 facts from the recipes$/);
    expect(badge.querySelector("[title]")?.getAttribute("title")).toBe(
      "Rendered by the server from the recipes: 1 name, 2 amounts and 1 alcohol label",
    );
  });

  it("says 1 fact in the singular", () => {
    render(<GroundingBadge domain="books" grounding={report({ rendered: { name: 1 } })} />);
    expect(screen.getByTestId("grounding-badge")).toHaveTextContent(/^1 fact from the catalog$/);
  });

  it("names computed totals and differences", () => {
    expect(describeCounts({ total: 2, diff: 1, recipe: 1 }, 4)).toBe(
      "1 recipe, 2 totals and 1 difference",
    );
    expect(renderedTotal(report({ rendered: { name: 3, total: 1 } }))).toBe(4);
    expect(renderedTotal(report({ rendered: undefined }))).toBe(0);
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

  it("keeps an older server's report without rendered facts as it was", () => {
    render(
      <GroundingBadge
        domain="evening"
        grounding={report({ checked: 2, counts: { price: 2 }, rendered: undefined })}
      />,
    );
    expect(screen.getByTestId("grounding-badge")).toHaveTextContent(
      "Checked 2 prices against the catalogs",
    );
  });

  it("tells what the fallback shows from the catalog", () => {
    render(
      <GroundingBadge
        domain="fashion"
        grounding={report({
          status: "fallback",
          checked: 1,
          issues: ["product 9 was not returned"],
          counts: { price: 1 },
          rendered: { name: 2, colour: 2, price: 2 },
        })}
      />,
    );
    const badge = screen.getByTestId("grounding-badge");
    expect(badge).toHaveTextContent("Unverified answer replaced with catalog facts");
    expect(badge.querySelector("[title]")?.getAttribute("title")).toBe(
      "1 claim did not match the tools' results; checked 1 price; shown from the catalog: " +
        "2 prices, 2 names and 2 colours",
    );
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
