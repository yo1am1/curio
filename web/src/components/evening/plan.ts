/** Plan my evening: the swap parts, the chat prompts and the save options. */
import type { EveningPlan, OutfitSlot } from "../../api/types";
import { alcoholLabel, euro, primaryAuthor, productName } from "../../domains/format";
import { type ApprovalOption, optionKey } from "../../state/approval";

export const OCCASIONS = [
  "Rainy Sunday in",
  "Date night",
  "Book club",
  "Summer garden party",
  "First date dinner",
  "Murder mystery night",
  "Game night with friends",
] as const;

export type Part = "outfit" | "book" | "drink";
export type SwapMode = "other" | "cheaper" | "alcohol_free";

/** What the plan's buttons do: on the planner page they call the API; in chat they ask the
 * agent with the same words the Streamlit client sends. */
export type PlanActions =
  | {
      kind: "page";
      swap: (part: Part, slot?: OutfitSlot["slot"], mode?: SwapMode) => void;
      busy: string | null;
      save: () => void;
    }
  | { kind: "chat"; send: (message: string) => void; active: boolean };

export const ANOTHER_BOOK = "Another book";
export const ANOTHER_DRINK = "Another drink";
export const ALCOHOL_FREE_DRINK = "Make the drink alcohol-free";
export const SAVE_EVENING = "Save the evening";

/** Whether an answer already gives the plan's "why it works" line. */
export function saysWhy(text: string, plan: EveningPlan): boolean {
  const folded = text.toLowerCase();
  return folded.includes("why it works") || folded.includes(plan.why.toLowerCase());
}

/** The plan's parts, as approval rows (each goes to its own list). */
export function eveningOptions(plan: EveningPlan): ApprovalOption[] {
  const options: ApprovalOption[] = [];
  for (const slot of plan.outfit?.outfit.slots ?? []) {
    const product = slot.product;
    if (!product) continue;
    options.push({
      key: optionKey("product_ids", product.product_id),
      field: "product_ids",
      value: product.product_id,
      label: `${productName(product)} · ${euro(product.price)}`,
      item: { kind: "product", product },
    });
  }
  if (plan.book) {
    const book = plan.book.book;
    options.push({
      key: optionKey("book_ids", book.book_id),
      field: "book_ids",
      value: book.book_id,
      label: `${book.title} · ${primaryAuthor(book)}`,
      item: { kind: "book", book },
    });
  }
  if (plan.drink) {
    const drink = plan.drink.cocktail;
    options.push({
      key: optionKey("drink_ids", drink.drink_id),
      field: "drink_ids",
      value: drink.drink_id,
      label: `${drink.name} · ${alcoholLabel(drink)}`,
      item: { kind: "cocktail", cocktail: drink },
    });
  }
  return options;
}
