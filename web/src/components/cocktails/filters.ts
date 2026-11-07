import { useHealth } from "../../api/queries";
import type { AlcoholFilter as Alcohol } from "../../api/types";
import { oneOf } from "../../state/pageState";

export const ALCOHOL_OPTIONS: readonly { value: Alcohol; label: string }[] = [
  { value: "any", label: "Any" },
  { value: "alcoholic", label: "Alcoholic" },
  { value: "non_alcoholic", label: "Alcohol-free" },
];

export const isAlcohol = oneOf<Alcohol>(["any", "alcoholic", "non_alcoholic"]);

export function useCocktailIntro(): string {
  const count = useHealth().data?.cocktail_count;
  return count
    ? `Search ${count.toLocaleString("en-US")} recipes by flavour or ingredient, or start from what you have.`
    : "Search recipes by flavour or ingredient, or start from what you have.";
}
