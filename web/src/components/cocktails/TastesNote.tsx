import { Sparkles } from "lucide-react";

import type { CocktailMemory } from "../../state/session";
import browse from "../browse/Browse.module.css";

/** Which remembered tastes the page applies, so a filtered result is never a surprise. */
export function TastesNote({
  tastes,
  pantry = false,
}: {
  tastes: CocktailMemory;
  pantry?: boolean;
}) {
  const parts: string[] = [];
  if (tastes.alcohol_free) parts.push("alcohol-free unless you choose otherwise");
  if (!pantry && tastes.avoided_ingredients.length) {
    parts.push(`no ${tastes.avoided_ingredients.join(", ")}`);
  }
  if (!parts.length) return null;
  return (
    <p className={browse.note} data-testid="tastes-note">
      <Sparkles size={14} aria-hidden="true" />
      Your remembered tastes apply: {parts.join(" · ")}.
    </p>
  );
}
