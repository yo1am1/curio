import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { approvalOptions, editedArgs } from "../../state/approval";
import { book, cocktail, product, proposal } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { ApprovalDrawer } from "./ApprovalDrawer";

describe("ApprovalDrawer", () => {
  it("approves the proposal unchanged with edited_args null", async () => {
    const onDecide = vi.fn();
    const action = proposal(
      "SaveToShortlist",
      { product_ids: [1, 2] },
      { products: [product(1), product(2)] },
    );
    renderApp(<ApprovalDrawer action={action} onDecide={onDecide} />);
    expect(screen.getByRole("heading", { name: "Needs your approval" })).toBeInTheDocument();
    expect(screen.getByText("Proposed: Would save them.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onDecide).toHaveBeenCalledWith(true, null);
  });

  it("narrows by unticking items: only the changed field is sent", async () => {
    const onDecide = vi.fn();
    const action = proposal(
      "SaveToShortlist",
      { product_ids: [1, 2, 3] },
      { products: [product(1), product(2, { price: 24.99 }), product(3)] },
    );
    renderApp(<ApprovalDrawer action={action} onDecide={onDecide} />);
    await userEvent.click(screen.getByRole("checkbox", { name: "Piece 2 · €24.99" }));
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onDecide).toHaveBeenCalledWith(true, { product_ids: [1, 3] });
  });

  it("narrows remembered preferences, a budget and alcohol-free", async () => {
    const onDecide = vi.fn();
    const action = proposal("RememberPreferences", {
      sections: ["Menswear"],
      colours: ["Black", "Blue"],
      max_price: 40,
    });
    renderApp(<ApprovalDrawer action={action} onDecide={onDecide} />);
    await userEvent.click(screen.getByRole("checkbox", { name: "Blue" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Budget up to €40" }));
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onDecide).toHaveBeenCalledWith(true, { colours: ["Black"], max_price: null });

    const tastes = proposal("RememberTastes", { alcohol_free: true, avoided_ingredients: ["Gin"] });
    expect(approvalOptions(tastes).map((o) => o.label)).toEqual([
      "Avoids Gin",
      "Alcohol-free drinks only",
    ]);
    const unticked = new Set([approvalOptions(tastes)[1]?.key ?? ""]);
    expect(editedArgs(tastes, unticked)).toEqual({ alcohol_free: false });
  });

  it("declines without edits, even after unticking", async () => {
    const onDecide = vi.fn();
    const action = proposal(
      "SaveCocktails",
      { drink_ids: [7, 8] },
      { cocktails: [cocktail(7), cocktail(8)] },
    );
    renderApp(<ApprovalDrawer action={action} onDecide={onDecide} />);
    await userEvent.click(screen.getByRole("checkbox", { name: "Drink 7 · Alcoholic" }));
    await userEvent.click(screen.getByRole("button", { name: "Decline" }));
    expect(onDecide).toHaveBeenCalledWith(false, null);
  });

  it("cannot approve once everything is unticked", async () => {
    const action = proposal("SaveBooks", { book_ids: [5] }, { books: [book(5)] });
    renderApp(<ApprovalDrawer action={action} onDecide={vi.fn()} />);
    await userEvent.click(screen.getByRole("checkbox", { name: "Book 5 · Jane Austen" }));
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
  });

  it("lists every narrowable value", () => {
    const action = proposal(
      "SaveEvening",
      { product_ids: [1, 99], book_ids: [5], drink_ids: [7] },
      {
        products: [product(1)],
        books: [book(5)],
        cocktails: [cocktail(7, { alcoholic: "Non alcoholic" })],
      },
    );
    // Product 99 has no card, so it is not listed, and is kept.
    expect(approvalOptions(action).map((o) => o.label)).toEqual([
      "Piece 1 · €19.99",
      "Drink 7 · Alcohol-free",
      "Book 5 · Jane Austen",
    ]);
    const reading = proposal("RememberReadingTastes", {
      liked_genres: ["Fantasy"],
      avoided_genres: ["Horror"],
      avoided_authors: ["Stephen King"],
    });
    expect(approvalOptions(reading).map((o) => o.label)).toEqual([
      "Likes Fantasy",
      "No Horror",
      "No books by Stephen King",
    ]);
    const unticked = new Set([approvalOptions(action)[0]?.key ?? ""]);
    expect(editedArgs(action, unticked)).toEqual({ product_ids: [99] });
  });

  it("shows why the last decision failed", () => {
    const action = proposal("SaveBooks", { book_ids: [5] }, { books: [book(5)] });
    renderApp(
      <ApprovalDrawer action={action} onDecide={vi.fn()} error="This proposal has expired." />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("This proposal has expired.");
  });
});
