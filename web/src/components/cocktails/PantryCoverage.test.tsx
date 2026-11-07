import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { cocktail } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { CocktailCard, ItemGrid } from "../cards/ItemCards";
import { PantryCoverage } from "./PantryCoverage";

describe("PantryCoverage", () => {
  it("shows how much of the recipe you have, and what is missing", () => {
    renderApp(
      <PantryCoverage
        match={{ have: ["Gin", "Lime"], missing: ["Sugar syrup"], coverage: 2 / 3 }}
      />,
    );
    const meter = screen.getByRole("meter", { name: "You have 2 of 3 ingredients" });
    expect(meter).toHaveAttribute("aria-valuenow", "67");
    const have = screen.getByRole("list", { name: "Ingredients you have" });
    expect(
      within(have)
        .getAllByRole("listitem")
        .map((li) => li.textContent),
    ).toEqual(["Gin", "Lime"]);
    const missing = screen.getByRole("list", { name: "Missing ingredients" });
    expect(missing).toHaveTextContent("Sugar syrup");
    expect(screen.queryByText("You can make this now.")).not.toBeInTheDocument();
  });

  it("says when you can make the drink now", () => {
    renderApp(
      <ItemGrid label="Drinks" kind="cocktail">
        <CocktailCard
          cocktail={cocktail(1, { name: "Gin Tonic" })}
          detailed
          extra={<PantryCoverage match={{ have: ["Gin", "Tonic"], missing: [], coverage: 1 }} />}
        />
      </ItemGrid>,
    );
    const card = screen.getByTestId("cocktail-card");
    expect(within(card).getByText("You can make this now.")).toBeInTheDocument();
    expect(within(card).getByRole("meter")).toHaveAttribute("aria-valuenow", "100");
    expect(within(card).queryByRole("list", { name: "Missing ingredients" })).toBeNull();
    expect(card).toHaveTextContent("Cocktail · Highball glass");
  });
});
