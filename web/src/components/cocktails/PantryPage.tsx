import { usePantry, useCocktailIngredients } from "../../api/queries";
import type { AlcoholFilter as Alcohol } from "../../api/types";
import { LIMITS, pantryBody } from "../../domains/requests";
import { isStringList, oneOf, usePageState } from "../../state/pageState";
import { useSession } from "../../state/stores";
import { Combobox } from "../../ui/Combobox";
import { Segmented } from "../../ui/Segmented";
import { CocktailCard, ItemGrid } from "../cards/ItemCards";
import browse from "../browse/Browse.module.css";
import { Results } from "../browse/Results";
import { PageHeader } from "../shell/PageHeader";
import { AlcoholFilter } from "./AlcoholFilter";
import { isAlcohol, useCocktailIntro } from "./filters";
import styles from "./Cocktails.module.css";
import { PantryCoverage } from "./PantryCoverage";
import { TastesNote } from "./TastesNote";

const MISSING = ["0", "1", "2", "3"] as const;
type Missing = (typeof MISSING)[number];

/** "What can I make?": the ingredients you have, how many may be missing, and the drinks. */
export function PantryPage() {
  const ingredients = useCocktailIngredients();
  const tastes = useSession((s) => s.cocktailPreferences);
  const intro = useCocktailIntro();
  const [pantry, setPantry] = usePageState<string[]>("pantry.have", [], isStringList);
  const [missing, setMissing] = usePageState<Missing>("pantry.missing", "2", oneOf(MISSING));
  const [alcohol, setAlcohol] = usePageState<Alcohol>("pantry.alcohol", "any", isAlcohol);
  const body = pantryBody({ ingredients: pantry, alcohol, maxMissing: Number(missing) }, tastes);
  const matches = usePantry(body);

  return (
    <div className={browse.page}>
      <PageHeader title="Cocktails" documentTitle="What can I make? · Cocktails" intro={intro} />
      <section className={browse.panel} aria-label="What can I make?">
        <div className={browse.panelHead}>
          <h2 className={browse.panelTitle}>What can I make?</h2>
          <p className={browse.panelIntro}>
            Pick what you have. Curio finds drinks you can make now or almost.
          </p>
        </div>
        <Combobox
          label="Ingredients you have"
          options={ingredients.data ?? []}
          value={pantry}
          onChange={setPantry}
          max={LIMITS.pantry}
          placeholder={
            ingredients.isPending ? "Loading ingredients…" : "e.g. Gin, Lime, Tonic water"
          }
        />
        <div className={styles.pantryControls}>
          <div className={browse.control}>
            <span className={browse.controlLabel} aria-hidden="true">
              Missing ingredients allowed
            </span>
            <Segmented
              label="Missing ingredients allowed"
              options={MISSING.map((value) => ({ value, label: value }))}
              value={missing}
              onChange={setMissing}
            />
          </div>
          <AlcoholFilter value={alcohol} onChange={setAlcohol} />
        </div>
        <TastesNote tastes={tastes} pantry />
      </section>
      <Results
        title="Drinks you can make"
        query={matches}
        enabled={body !== null}
        kind="cocktail"
        noun="drink"
        idle="Add at least one ingredient to see what you can make."
        empty="No recipes match yet. Allow more missing ingredients or add what you have."
      >
        {(items) => (
          <ItemGrid label="Drinks you can make" kind="cocktail">
            {items.map((match) => (
              <CocktailCard
                key={match.cocktail.drink_id}
                cocktail={match.cocktail}
                detailed
                extra={<PantryCoverage match={match} />}
              />
            ))}
          </ItemGrid>
        )}
      </Results>
    </div>
  );
}
