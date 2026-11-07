import type { AgentStep, Book, Cocktail, PendingAction, Product } from "../api/types";

export const product = (id: number, overrides: Partial<Product> = {}): Product => ({
  product_id: id,
  name: `PQ PIECE ${id}`,
  display_name: `Piece ${id}`,
  product_type: "Trousers",
  product_group: "Garment Lower body",
  section: "Menswear",
  colour: "Black",
  description: "Woven trousers.",
  price: 19.99,
  shopify_category: "Apparel & Accessories > Clothing > Pants",
  ...overrides,
});

export const cocktail = (id: number, overrides: Partial<Cocktail> = {}): Cocktail => ({
  drink_id: id,
  name: `Drink ${id}`,
  alcoholic: "Alcoholic",
  category: "Cocktail",
  glass: "Highball glass",
  instructions: "Stir.",
  ingredients: [{ name: "Gin", measure: "4 cl" }],
  image_url: `https://www.thecocktaildb.com/images/${id}.jpg`,
  source_url: "https://www.thecocktaildb.com/",
  ...overrides,
});

export const book = (id: number, overrides: Partial<Book> = {}): Book => ({
  book_id: id,
  title: `Book ${id}`,
  authors: ["Jane Austen", "An Illustrator"],
  average_rating: 4.24,
  ratings_count: 2035490,
  description: "A novel.",
  genres: ["Classics", "Romance"],
  image_url: `https://images.gr-assets.com/books/1320399351m/${id}.jpg`,
  isbn: "9780679783268",
  source_url: `https://www.goodreads.com/book/show/${id}`,
  readers: 100,
  ...overrides,
});

export const step = (overrides: Partial<AgentStep> = {}): AgentStep => ({
  kind: "tool",
  name: "SearchCatalog",
  summary: "Found 5 matches.",
  status: "ok",
  detail: {},
  duration_ms: 200,
  ...overrides,
});

export const proposal = (
  tool: PendingAction["tool"],
  args: Record<string, unknown>,
  cards: Partial<Pick<PendingAction, "products" | "cocktails" | "books">> = {},
): PendingAction => ({
  id: "abc123",
  tool,
  args,
  request: "Save them",
  issued_at: 1794053174,
  signature: "sig",
  summary: "Proposed: Would save them.",
  products: [],
  cocktails: [],
  books: [],
  ...cards,
});

/** A fetch Response streaming `chunks` (as the browser would deliver them). */
export function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}
