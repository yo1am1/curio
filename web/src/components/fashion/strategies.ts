/** The fashion feed's strategies and their evidence summaries. */
import type { Strategy } from "../../api/queries";

export interface StrategyInfo {
  id: Strategy;
  label: string;
  description: string;
}

export const STRATEGIES: readonly StrategyInfo[] = [
  {
    id: "learned",
    label: "Personalized ranking",
    description: "Favorites and new finds, ranked by your purchase patterns and recent demand.",
  },
  {
    id: "repeat",
    label: "Buy again",
    description: "Your most recent purchases first, followed by recent best-sellers.",
  },
  {
    id: "collaborative",
    label: "Bought together with your items",
    description: "New pieces bought by shoppers with items like yours.",
  },
  {
    id: "content",
    label: "Visual style",
    description: "New pieces with photos close to the styles in your history.",
  },
  {
    id: "graph",
    label: "Purchase connections",
    description: "New pieces connected through purchases and catalog attributes.",
  },
];

/** Strategies offered: personalized ranking only when the server has a trained ranker. */
export function availableStrategies(rankingEnabled: boolean): StrategyInfo[] {
  return STRATEGIES.filter((s) => rankingEnabled || s.id !== "learned");
}

/** Display names of the strategies in the evaluation results (scripts.evaluate). */
export const EVALUATED_NAMES: Record<string, string> = {
  learned: "Personalized ranking",
  repeat: "Buy again + best-sellers",
  popularity: "Recent best-sellers",
  popularity_novel: "Best-sellers, new items only",
  content: "Visual style (CLIP)",
  collaborative: "Collaborative",
  graph: "Purchase connections",
};

/** The strongest supplied evidence as one line (the raw evidence stays in "Why this pick"). */
export function recommendationReason(reasons: readonly string[]): string | null {
  const first = reasons[0];
  if (first?.startsWith("Buy again:")) return first;
  // Collaborative: the purchase that points here most strongly.
  if (first && (first.includes(" who bought ") || first.startsWith("Popular in"))) return first;
  for (const reason of reasons) {
    if (reason.startsWith("Bought together with")) {
      return reason.replace(/\s*\(product \d+\)|\s*\(lift [^)]+\)/g, "");
    }
  }
  const any = (prefix: string) => reasons.some((reason) => reason.startsWith(prefix));
  if (any("Bought on the same day")) return "Bought together by the same shoppers.";
  if (any("Same specific Shopify category")) {
    return "Same category, type and section, at a higher approximate price.";
  }
  if (any("Shares Shopify category")) return "Shares a product category with a previous purchase.";
  if (any("Shares ")) return "Connected through shared catalog attributes.";
  if (any("Popularity fallback")) {
    return "Popular in recent purchases; no personal graph connections were found.";
  }
  return null;
}
