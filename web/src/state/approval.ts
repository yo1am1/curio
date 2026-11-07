/** Narrowing a proposal before approving it, exactly as the Streamlit client does
 * (`approval_options` and `queue_decision` in src/curio/app/ui/chat.py): list values can be
 * dropped one by one, a budget can be dropped, and alcohol-free can be turned off. */
import type { Book, Cocktail, PendingAction, Product } from "../api/types";
import { alcoholLabel, euro, primaryAuthor, productName } from "../domains/format";

/** Proposal arguments the shopper may narrow: list values can be dropped one by one. */
export const APPROVAL_LIST_FIELDS = [
  "product_ids",
  "sections",
  "colours",
  "drink_ids",
  "liked_ingredients",
  "avoided_ingredients",
  "book_ids",
  "liked_genres",
  "avoided_genres",
  "avoided_authors",
] as const;

export type ApprovalItem =
  | { kind: "product"; product: Product }
  | { kind: "cocktail"; cocktail: Cocktail }
  | { kind: "book"; book: Book };

export interface ApprovalOption {
  key: string;
  field: string;
  value: unknown;
  label: string;
  item?: ApprovalItem;
}

export function optionKey(field: string, value: unknown): string {
  return `${field}:${JSON.stringify(value)}`;
}

type Args = Record<string, unknown>;

function values(args: Args, field: string): unknown[] {
  const value = args[field];
  return Array.isArray(value) ? value : [];
}

/** A checkbox row for every value the shopper may drop. Card IDs the proposal shows no card
 * for are not listed (and are kept). */
export function approvalOptions(action: PendingAction): ApprovalOption[] {
  const args: Args = action.args;
  const products = new Map((action.products ?? []).map((p) => [p.product_id, p]));
  const cocktails = new Map((action.cocktails ?? []).map((c) => [c.drink_id, c]));
  const books = new Map((action.books ?? []).map((b) => [b.book_id, b]));
  const options: ApprovalOption[] = [];
  for (const field of APPROVAL_LIST_FIELDS) {
    for (const value of values(args, field)) {
      const key = optionKey(field, value);
      if (field === "product_ids") {
        const product = products.get(value as number);
        if (!product) continue;
        options.push({
          key,
          field,
          value,
          label: `${productName(product)} · ${euro(product.price)}`,
          item: { kind: "product", product },
        });
      } else if (field === "drink_ids") {
        const cocktail = cocktails.get(value as number);
        if (!cocktail) continue;
        options.push({
          key,
          field,
          value,
          label: `${cocktail.name} · ${alcoholLabel(cocktail)}`,
          item: { kind: "cocktail", cocktail },
        });
      } else if (field === "book_ids") {
        const book = books.get(value as number);
        if (!book) continue;
        options.push({
          key,
          field,
          value,
          label: `${book.title} · ${primaryAuthor(book)}`,
          item: { kind: "book", book },
        });
      } else {
        const text = String(value);
        const label =
          field === "liked_ingredients" || field === "liked_genres"
            ? `Likes ${text}`
            : field === "avoided_genres"
              ? `No ${text}`
              : field === "avoided_authors"
                ? `No books by ${text}`
                : field === "avoided_ingredients"
                  ? `Avoids ${text}`
                  : text;
        options.push({ key, field, value, label });
      }
    }
  }
  if (args.max_price != null) {
    options.push({
      key: optionKey("max_price", args.max_price),
      field: "max_price",
      value: args.max_price,
      label: `Budget up to €${typeof args.max_price === "number" ? args.max_price : JSON.stringify(args.max_price)}`,
    });
  }
  if (args.alcohol_free) {
    options.push({
      key: optionKey("alcohol_free", true),
      field: "alcohol_free",
      value: true,
      label: "Alcohol-free drinks only",
    });
  }
  return options;
}

/** The `edited_args` of an approval: only the fields the shopper changed, or null when
 * nothing was unticked. */
export function editedArgs(
  action: PendingAction,
  unticked: ReadonlySet<string>,
): Record<string, unknown> | null {
  const args: Args = action.args;
  const kept = (field: string, value: unknown) => !unticked.has(optionKey(field, value));
  const narrowed: Args = {};
  for (const field of APPROVAL_LIST_FIELDS) {
    if (field in args) narrowed[field] = values(args, field).filter((v) => kept(field, v));
  }
  if ("max_price" in args && !kept("max_price", args.max_price)) narrowed.max_price = null;
  if (args.alcohol_free && !kept("alcohol_free", true)) narrowed.alcohol_free = false;
  const edits = Object.fromEntries(
    Object.entries(narrowed).filter(
      ([field, value]) => JSON.stringify(value) !== JSON.stringify(args[field]),
    ),
  );
  return Object.keys(edits).length ? edits : null;
}

/** Whether anything is left to approve once `unticked` values are dropped. */
export function keepsSomething(options: ApprovalOption[], unticked: ReadonlySet<string>): boolean {
  return options.length === 0 || options.some((option) => !unticked.has(option.key));
}
