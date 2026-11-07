import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { step } from "../../test/fixtures";
import { StepTrace } from "./StepTrace";
import { traceLabel } from "./trace";

const steps = [
  step({
    kind: "plan",
    name: "Understand request",
    summary: "Looking for black trousers",
    duration_ms: 11,
  }),
  step({ kind: "tool", name: "SearchCatalog", summary: "Found 5 matches.", duration_ms: 205 }),
  step({ kind: "answer", name: "Draft answer", summary: "Drafted a reply.", duration_ms: 4 }),
  step({
    kind: "guardrail",
    name: "Grounding check",
    summary: "Verified 4 prices.",
    duration_ms: 0,
  }),
];

describe("StepTrace", () => {
  it("counts tool calls and proposals as actions, with the total time", () => {
    expect(traceLabel(steps)).toBe("How Curio worked · 1 action · 0.2s");
    expect(traceLabel([...steps, step({ kind: "approval", name: "SaveToShortlist" })])).toBe(
      "How Curio worked · 2 actions · 0.4s",
    );
  });

  it("folds a finished turn's steps and opens them on demand", async () => {
    render(<StepTrace steps={steps} />);
    const toggle = screen.getByRole("button", { name: /How Curio worked · 1 action · 0.2s/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Search catalog")).not.toBeInTheDocument();
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    // Friendly labels, the summary and the timing of steps over 0.1 s.
    expect(screen.getByText("Plan")).toBeInTheDocument();
    expect(screen.getByText("Search catalog")).toBeInTheDocument();
    expect(screen.getByText("Grounding check")).toBeInTheDocument();
    expect(screen.getByText("Found 5 matches.")).toBeInTheDocument();
    expect(screen.getByText("0.2s")).toBeInTheDocument();
  });

  it("shows steps live as they stream, naming the current one", () => {
    const { rerender } = render(<StepTrace steps={[]} live />);
    expect(screen.getByText("Curio is planning…")).toBeInTheDocument();
    rerender(<StepTrace steps={steps.slice(0, 2)} live />);
    expect(screen.getByText("Search catalog…")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    rerender(<StepTrace steps={steps} live />);
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getByText("Verified 4 prices.")).toBeInTheDocument();
  });

  it("marks failed and pending steps", () => {
    render(
      <StepTrace
        live
        steps={[
          step({ status: "error", summary: "The search failed." }),
          step({
            kind: "approval",
            name: "SaveToShortlist",
            status: "pending",
            summary: "Waiting.",
          }),
        ]}
      />,
    );
    expect(screen.getByText("failed")).toBeInTheDocument();
    expect(screen.getByText("waiting for you")).toBeInTheDocument();
    expect(screen.getByText("Shortlist proposal")).toBeInTheDocument();
  });
});
