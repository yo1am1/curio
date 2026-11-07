import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { Combobox } from "./Combobox";
import { filterOptions } from "./filterOptions";

const INGREDIENTS = ["Gin", "Lime", "Lime Juice", "Bacardi Limon", "Mint", "Tonic water"];

function Picker({ max, multiple = true }: { max?: number; multiple?: boolean }) {
  const [value, setValue] = useState<string[]>([]);
  return (
    <>
      <Combobox
        label="Must include"
        options={INGREDIENTS}
        value={value}
        onChange={setValue}
        max={max}
        multiple={multiple}
      />
      <output>{value.join(",")}</output>
    </>
  );
}

describe("Combobox", () => {
  it("ranks prefix matches before matches inside a word", () => {
    expect(filterOptions(INGREDIENTS, "lim", (o) => o)).toEqual([
      "Lime",
      "Lime Juice",
      "Bacardi Limon",
    ]);
  });

  it("filters, moves with the arrow keys and picks with Enter", async () => {
    render(<Picker />);
    const box = screen.getByRole("combobox", { name: "Must include" });
    expect(box).toHaveAttribute("aria-expanded", "false");
    await userEvent.type(box, "lim");
    expect(box).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Lime",
      "Lime Juice",
      "Bacardi Limon",
    ]);
    await userEvent.keyboard("{ArrowDown}");
    const active = box.getAttribute("aria-activedescendant");
    expect(document.getElementById(active ?? "")).toHaveTextContent("Lime Juice");
    await userEvent.keyboard("{Enter}");
    expect(screen.getByText("Lime Juice", { selector: "output" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove Lime Juice" })).toBeInTheDocument();
    // The query clears for the next pick; Backspace on an empty input removes the last chip.
    expect(box).toHaveValue("");
    await userEvent.keyboard("{Backspace}");
    expect(screen.queryByRole("button", { name: "Remove Lime Juice" })).toBeNull();
  });

  it("marks chosen options, closes on Escape and respects the limit", async () => {
    render(<Picker max={1} />);
    const box = screen.getByRole("combobox", { name: "Must include" });
    await userEvent.click(box);
    await userEvent.click(screen.getByRole("option", { name: "Gin" }));
    await userEvent.click(box);
    expect(screen.getByRole("option", { name: "Gin" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("option", { name: "Mint" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("You can choose up to 1.")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(box).toHaveAttribute("aria-expanded", "false");
  });

  it("shows a single choice in the input and clears it", async () => {
    render(<Picker multiple={false} />);
    const box = screen.getByRole("combobox", { name: "Must include" });
    await userEvent.type(box, "min{Enter}");
    expect(box).toHaveValue("Mint");
    await userEvent.click(screen.getByRole("button", { name: "Clear must include" }));
    expect(box).toHaveValue("");
  });
});
