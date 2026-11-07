import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { cocktail, product } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { createAppStores } from "../../state/stores";
import { SessionPanel } from "../session/SessionPanel";
import { cheapest, sharedIngredients, toggleSelection } from "./compare";

function storesWith(change: (s: ReturnType<typeof createAppStores>) => void) {
  const stores = createAppStores(localStorage, sessionStorage);
  change(stores);
  return stores;
}

describe("compare helpers", () => {
  it("finds the cheapest pieces and the ingredients every recipe shares", () => {
    expect(
      cheapest([
        product(1, { price: 9.99 }),
        product(2, { price: 4.5 }),
        product(3, { price: 4.5 }),
      ]),
    ).toEqual(new Set([2, 3]));
    expect(
      sharedIngredients([
        cocktail(1, { ingredients: [{ name: "Gin" }, { name: "Lime Juice" }, { name: "Sugar" }] }),
        cocktail(2, { ingredients: [{ name: "lime juice" }, { name: "Gin" }] }),
      ]),
    ).toEqual(["Gin", "Lime Juice"]);
  });

  it("keeps the saved order and at most four columns", () => {
    const ids = [1, 2, 3, 4, 5];
    expect(toggleSelection(ids, [3, 1], 2)).toEqual([1, 2, 3]);
    expect(toggleSelection(ids, [1, 2, 3, 4], 5)).toEqual([1, 2, 3, 4]);
    expect(toggleSelection(ids, [1, 2, 3, 4], 2)).toEqual([1, 3, 4]);
  });
});

describe("Compare dialogs", () => {
  it("opens from the session panel and compares the saved pieces", async () => {
    const stores = storesWith((s) => {
      s.session.set((session) => ({
        ...session,
        shortlist: [
          product(1, { display_name: "Linen Shirt", price: 24.99, colour: "White" }),
          product(2, { display_name: "Chinos", price: 19.99, section: "Menswear" }),
        ],
      }));
    });
    renderApp(<SessionPanel domain="fashion" onAsk={() => undefined} />, stores);
    await userEvent.click(screen.getByRole("button", { name: "Compare" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Compare pieces" })).toBeInTheDocument();
    const columns = within(dialog).getAllByTestId("compare-piece");
    expect(columns).toHaveLength(2);
    expect(columns[0]).toHaveTextContent("Linen Shirt");
    expect(columns[0]).toHaveTextContent("Trousers · White · Menswear");
    expect(within(columns[1] as HTMLElement).getByText("Lowest price")).toBeInTheDocument();
    expect(within(columns[0] as HTMLElement).queryByText("Lowest price")).toBeNull();

    // A piece opens on top, with a way back to the comparison.
    await userEvent.click(within(dialog).getByRole("button", { name: "View Chinos" }));
    expect(await within(dialog).findByRole("button", { name: "Back" })).toBeInTheDocument();
  });

  it("compares drinks, marks the shared ingredients and limits the choice to four", async () => {
    const drinks = [1, 2, 3, 4, 5].map((id) =>
      cocktail(id, {
        ingredients: [{ name: "Lime", measure: "1" }, { name: `Spirit ${String(id)}` }],
      }),
    );
    const stores = storesWith((s) => {
      s.session.set((session) => ({ ...session, cocktailShortlist: drinks }));
    });
    renderApp(<SessionPanel domain="cocktails" onAsk={() => undefined} />, stores);
    await userEvent.click(screen.getByRole("button", { name: "Compare" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Compare drinks" })).toBeInTheDocument();
    expect(within(dialog).getAllByTestId("compare-drink")).toHaveLength(4);
    expect(within(dialog).getByTestId("shared-ingredients")).toHaveTextContent(
      "In every recipeLime",
    );
    const lime = within(dialog).getAllByText("Lime")[1]?.closest("li");
    expect(lime).toHaveAttribute("data-shared", "true");

    // Five saved: choose which four.
    expect(within(dialog).getByRole("button", { name: "Drink 5" })).toBeDisabled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Drink 1" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Drink 5" }));
    const names = within(dialog)
      .getAllByTestId("compare-drink")
      .map((column) => within(column).getByRole("heading").textContent);
    expect(names).toEqual(["Drink 2", "Drink 3", "Drink 4", "Drink 5"]);
  });

  it("offers Compare only with two saved items", () => {
    const stores = storesWith((s) => {
      s.session.set((session) => ({ ...session, shortlist: [product(1)] }));
    });
    renderApp(<SessionPanel domain="fashion" onAsk={() => undefined} />, stores);
    expect(screen.queryByRole("button", { name: "Compare" })).toBeNull();
  });
});
