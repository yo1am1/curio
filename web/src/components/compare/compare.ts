/** What the Compare dialogs work out: which saved items to show and what they share. */
import type { Cocktail, Product } from "../../api/types";

/** Columns side by side at most. */
export const MAX_COMPARE = 4;

/** Start with the first saved items, at most `MAX_COMPARE`. */
export function initialSelection(ids: readonly number[]): number[] {
  return ids.slice(0, MAX_COMPARE);
}

/** Toggle an item, keeping saved-list order and the column limit. */
export function toggleSelection(
  ids: readonly number[],
  selected: readonly number[],
  id: number,
): number[] {
  if (selected.includes(id)) return selected.filter((s) => s !== id);
  if (selected.length >= MAX_COMPARE) return [...selected];
  return ids.filter((s) => s === id || selected.includes(s));
}

/** The ids of the cheapest pieces (every piece at the lowest price). */
export function cheapest(products: readonly Pick<Product, "product_id" | "price">[]): Set<number> {
  if (!products.length) return new Set();
  const low = Math.min(...products.map((p) => p.price));
  return new Set(products.filter((p) => p.price === low).map((p) => p.product_id));
}

const key = (name: string) => name.trim().toLocaleLowerCase();

/** Ingredients every recipe lists (case-insensitive), in the first recipe's spelling and
 * alphabetical order. */
export function sharedIngredients(cocktails: readonly Pick<Cocktail, "ingredients">[]): string[] {
  const [first, ...rest] = cocktails;
  if (!first || !rest.length) return [];
  const others = rest.map((c) => new Set(c.ingredients.map((i) => key(i.name))));
  const shared = new Map<string, string>();
  for (const { name } of first.ingredients) {
    if (others.every((names) => names.has(key(name))) && !shared.has(key(name))) {
      shared.set(key(name), name);
    }
  }
  return [...shared.values()].sort((a, b) => a.localeCompare(b));
}

export function isShared(name: string, shared: readonly string[]): boolean {
  return shared.some((s) => key(s) === key(name));
}
