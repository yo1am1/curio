import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { book, cocktail, product } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { BookCard, CocktailCard, ItemGrid, ProductCard } from "./ItemCards";
import { OutfitCard } from "./OutfitCard";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("item cards", () => {
  it("shows a product by its display name, with price, colour and its photo", () => {
    renderApp(
      <ItemGrid label="Pieces">
        <ProductCard product={product(843614008, { display_name: "Jamie Skinny Cropped" })} />
      </ItemGrid>,
    );
    const card = screen.getByTestId("product-card");
    expect(within(card).getByText("Jamie Skinny Cropped")).toBeInTheDocument();
    expect(within(card).queryByText("PQ PIECE 843614008")).not.toBeInTheDocument();
    expect(card).toHaveTextContent("€19.99 · Black");
    expect(within(card).getByRole("img", { name: "Jamie Skinny Cropped" })).toHaveAttribute(
      "src",
      "/products/843614008/image",
    );
  });

  it("falls back to the placeholder when a photo fails to load", () => {
    renderApp(<ProductCard product={product(1)} />);
    fireEvent.error(screen.getByRole("img", { name: "Piece 1" }));
    expect(screen.getByText("No photo available.")).toBeInTheDocument();
  });

  it("saves a product to the shortlist once", async () => {
    const { stores } = renderApp(<ProductCard product={product(1)} />);
    await userEvent.click(screen.getByRole("button", { name: "Save Piece 1 to your shortlist" }));
    expect(stores.session.get().shortlist.map((p) => p.product_id)).toEqual([1]);
    expect(screen.getByRole("button", { name: "Piece 1 is on your shortlist" })).toBeDisabled();
    expect(await screen.findByText("Saved Piece 1 to your shortlist.")).toBeInTheDocument();
  });

  it("labels a drink's alcohol status and glass; no https photo gives the placeholder", () => {
    renderApp(
      <CocktailCard
        cocktail={cocktail(3, { alcoholic: "Non alcoholic", image_url: "http://x/1.jpg" })}
      />,
    );
    const card = screen.getByTestId("cocktail-card");
    expect(card).toHaveTextContent("Alcohol-free");
    expect(card).toHaveTextContent("Highball glass");
    expect(within(card).getByText("No photo available.")).toBeInTheDocument();
  });

  it("shows a book's first author and rating, with Goodreads' large cover", () => {
    renderApp(<BookCard book={book(10, { title: "Pride and Prejudice" })} />);
    const card = screen.getByTestId("book-card");
    expect(card).toHaveTextContent("Jane Austen");
    expect(card).not.toHaveTextContent("An Illustrator");
    expect(card).toHaveTextContent("4.24");
    expect(within(card).getByRole("img", { name: "Cover of Pride and Prejudice" })).toHaveAttribute(
      "src",
      "https://images.gr-assets.com/books/1320399351l/10.jpg",
    );
  });

  it("uses Open Library by ISBN for a book without a Goodreads cover, then the title", () => {
    renderApp(<BookCard book={book(11, { title: "Uncovered", image_url: null })} />);
    const cover = screen.getByRole("img", { name: "Cover of Uncovered" });
    expect(cover).toHaveAttribute(
      "src",
      "https://covers.openlibrary.org/b/isbn/9780679783268-M.jpg?default=false",
    );
    fireEvent.error(cover);
    expect(within(screen.getByTestId("book-card")).getAllByText("Uncovered")).toHaveLength(2);
  });

  it("opens the product dialog from a card", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve(
          new Response(
            JSON.stringify(
              url.startsWith("/products/1") && !/similar|related/.test(url) ? product(1) : [],
            ),
            {
              headers: { "content-type": "application/json" },
            },
          ),
        ),
      ),
    );
    renderApp(<ProductCard product={product(1)} />);
    await userEvent.click(screen.getByRole("button", { name: "View Piece 1" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByRole("heading", { name: "Piece 1" })).toBeInTheDocument();
    expect(within(dialog).getByText("€19.99")).toBeInTheDocument();
  });

  it("asks the agent for swaps and saving from an outfit card", async () => {
    const send = vi.fn();
    renderApp(
      <OutfitCard
        active
        send={send}
        outfit={{
          occasion: "summer wedding",
          budget: 80,
          total: 49.98,
          status: "complete",
          notes_carried: false,
          minimum_carried: false,
          state: { occasion: "summer wedding", pieces: [] },
          slots: [
            {
              slot: "dress",
              label: "Dress",
              product: product(1),
              reason: "",
              carried: false,
              issue: "",
            },
            {
              slot: "shoes",
              label: "Shoes",
              product: null,
              reason: "",
              carried: false,
              issue: "Nothing fits.",
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("€49.98 of €80.00 · €30.02 left")).toBeInTheDocument();
    expect(screen.getByText("Nothing fits.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Ask Curio for a cheaper dress" }));
    await userEvent.click(screen.getByRole("button", { name: "Save outfit" }));
    expect(send.mock.calls).toEqual([
      ["Cheaper dress for my outfit"],
      ["Save the whole outfit to my shortlist"],
    ]);
  });
});
