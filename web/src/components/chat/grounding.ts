import type { GroundingReport } from "../../api/types";

type Nouns = [string, string];
const FACTS: Nouns = ["fact", "facts"];
/** How each kind of checked fact reads, in the order the badge lists them. */
const KINDS: Record<string, Nouns> = {
  price: ["price", "prices"],
  title: ["title", "titles"],
  name: ["name", "names"],
  rating: ["rating", "ratings"],
  author: ["author", "authors"],
  amount: ["amount", "amounts"],
  alcohol: ["alcohol label", "alcohol labels"],
  pages: ["page count", "page counts"],
  year: ["year", "years"],
  percent: ["percentage", "percentages"],
  id: ["ID", "IDs"],
  colour: ["colour", "colours"],
  ingredient: ["ingredient", "ingredients"],
  recipe: ["recipe", "recipes"],
  total: ["total", "totals"],
  diff: ["difference", "differences"],
  claim: ["claim", "claims"],
  fact: ["fact", "facts"],
};
/** The badge names this many kinds; the rest are summed up as "other facts". */
const SHOWN_KINDS = 3;

function count(n: number, [one, many]: Nouns): string {
  return `${n} ${n === 1 ? one : many}`;
}

function list(parts: string[]): string {
  return parts.length < 2
    ? (parts[0] ?? "")
    : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** "3 prices and 2 names" for counts by kind; `total` when there are none (older servers). */
export function describeCounts(byKind: Record<string, number> | undefined, total: number): string {
  const counts = Object.entries(byKind ?? {}).filter(([, n]) => n > 0);
  if (counts.length === 0) return count(total, FACTS);
  const order = Object.keys(KINDS);
  const rank = (kind: string) => (order.includes(kind) ? order.indexOf(kind) : order.length);
  counts.sort(([a], [b]) => rank(a) - rank(b));
  const parts = counts
    .slice(0, SHOWN_KINDS)
    .map(([kind, n]) => count(n, KINDS[kind] ?? [kind, `${kind}s`]));
  const rest = counts.slice(SHOWN_KINDS).reduce((sum, [, n]) => sum + n, 0);
  if (rest > 0) parts.push(count(rest, ["other fact", "other facts"]));
  return list(parts);
}

/** What the backstop checked among the values the model typed: "3 prices and 2 names". */
export function describeChecked(grounding: GroundingReport): string {
  return describeCounts(grounding.counts, grounding.checked);
}

/** How many facts the server rendered from fact references (0 on older servers). */
export function renderedTotal(grounding: GroundingReport): number {
  return Object.values(grounding.rendered ?? {}).reduce((sum, n) => sum + (n > 0 ? n : 0), 0);
}

/** "5 facts" or "1 fact". */
export function facts(n: number): string {
  return count(n, FACTS);
}
