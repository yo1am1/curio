/** Display helpers shared by the cards, dialogs and chat. */
import type { Book, Cocktail, Product } from "../api/types";
import type { Tone } from "./registry";

export function euro(value: number): string {
  return `€${value.toFixed(2)}`;
}

/** "€94.96 of €100.00 · €5.04 left", or "Total €94.96" without a budget. */
export function budgetLine(total: number, budget: number | null | undefined): string {
  if (budget == null) return `Total ${euro(total)}`;
  const left = budget - total;
  const rest = left >= 0 ? `${euro(left)} left` : `${euro(-left)} over`;
  return `${euro(total)} of ${euro(budget)} · ${rest}`;
}

/** A product's name as shown: the API's cleaned `display_name`, else its raw name. */
export function productName(product: Pick<Product, "name"> & { display_name?: string }): string {
  return product.display_name || product.name;
}

const ALCOHOL_STATUS: Record<string, [string, Tone]> = {
  Alcoholic: ["Alcoholic", "orange"],
  "Non alcoholic": ["Alcohol-free", "green"],
  // Not alcohol-free: these recipes list alcoholic ingredients.
  "Optional alcohol": ["Alcohol optional", "blue"],
};

export function alcoholStatus(cocktail: Pick<Cocktail, "alcoholic">): [string, Tone] {
  return ALCOHOL_STATUS[cocktail.alcoholic] ?? [cocktail.alcoholic, "gray"];
}

export function alcoholLabel(cocktail: Pick<Cocktail, "alcoholic">): string {
  return alcoholStatus(cocktail)[0];
}

export function isAlcoholFree(cocktail: Pick<Cocktail, "alcoholic">): boolean {
  return cocktail.alcoholic === "Non alcoholic";
}

/** The first listed author: Goodreads lists illustrators and translators after the author. */
export function primaryAuthor(book: Pick<Book, "authors">): string {
  return book.authors[0] ?? "Unknown author";
}

export function rating(book: Pick<Book, "average_rating">): string {
  return book.average_rating.toFixed(2);
}

export function ratingLine(book: Pick<Book, "average_rating" | "ratings_count">): string {
  return `${rating(book)} · ${book.ratings_count.toLocaleString("en-US")} Goodreads ratings`;
}

/** Year, length and series, as one line. */
export function bookFacts(book: Book): string {
  const facts: string[] = [];
  if (book.year != null) facts.push(book.year > 0 ? String(book.year) : `${-book.year} BC`);
  if (book.pages) facts.push(`${book.pages} pages`);
  if (book.series) {
    facts.push(book.series_position ? `${book.series} #${book.series_position}` : book.series);
  }
  return facts.join(" · ");
}

const MEDIUM_COVER = /(\/books\/\d+)m\//;
const ISBN13 = /^97[89]\d{10}$/;
export const OPEN_LIBRARY_COVER = "https://covers.openlibrary.org/b/isbn/{}-M.jpg?default=false";
export const OPEN_LIBRARY_BOOK = "https://openlibrary.org/isbn/{}";

/** Cover candidates in order: Goodreads (https only; its large size for big slots), then
 * Open Library by a valid ISBN-13 (built here, never read from data). Open Library answers
 * a missing cover with a 404, so the image falls through to the placeholder. */
export function coverSources(
  book: Pick<Book, "image_url" | "isbn">,
  large = false,
): { url: string; openLibrary: boolean }[] {
  const sources: { url: string; openLibrary: boolean }[] = [];
  const url = book.image_url;
  if (url?.startsWith("https://")) {
    sources.push({ url: large ? url.replace(MEDIUM_COVER, "$1l/") : url, openLibrary: false });
  } else if (book.isbn && ISBN13.test(book.isbn)) {
    sources.push({ url: OPEN_LIBRARY_COVER.replace("{}", book.isbn), openLibrary: true });
  }
  return sources;
}

/** Recipe photos come straight from TheCocktailDB; only https is shown. */
export function drinkPhoto(cocktail: Pick<Cocktail, "image_url">): string | null {
  return cocktail.image_url?.startsWith("https://") ? cocktail.image_url : null;
}

export function capitalize(text: string): string {
  return text.slice(0, 1).toUpperCase() + text.slice(1);
}

export function plural(count: number, noun: string, many = `${noun}s`): string {
  return `${count} ${count === 1 ? noun : many}`;
}
