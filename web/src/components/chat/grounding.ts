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

/** "3 prices and 2 names", from the report's counts by kind (or its total on older servers). */
export function describeChecked(grounding: GroundingReport): string {
  const counts = Object.entries(grounding.counts ?? {}).filter(([, n]) => n > 0);
  if (counts.length === 0) return count(grounding.checked, FACTS);
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
