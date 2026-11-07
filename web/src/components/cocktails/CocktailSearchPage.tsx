import { Search } from "lucide-react";

import {
  useCocktailCategories,
  useCocktailIngredients,
  useCocktailSearch,
} from "../../api/queries";
import type { AlcoholFilter as Alcohol } from "../../api/types";
import { LIMITS, cocktailSearchBody } from "../../domains/requests";
import { isStringList, isStringOrNull, usePageState } from "../../state/pageState";
import { useSession } from "../../state/stores";
import { Button } from "../../ui/Button";
import { Combobox } from "../../ui/Combobox";
import { Field, Select, TextInput } from "../../ui/Field";
import { CocktailCard, ItemGrid } from "../cards/ItemCards";
import browse from "../browse/Browse.module.css";
import { Examples } from "../browse/Examples";
import { Results } from "../browse/Results";
import { PageHeader } from "../shell/PageHeader";
import { AlcoholFilter } from "./AlcoholFilter";
import styles from "./Cocktails.module.css";
import { TastesNote } from "./TastesNote";
import { isAlcohol, useCocktailIntro } from "./filters";

const EXAMPLES = ["Margarita", "Something with rum and lime", "Creamy coffee"];
/** Recipe search: a description, must-include ingredients, alcohol and category. */
export function CocktailSearchPage() {
  const ingredients = useCocktailIngredients();
  const categories = useCocktailCategories();
  const tastes = useSession((s) => s.cocktailPreferences);
  const intro = useCocktailIntro();
  const [draft, setDraft] = usePageState("drinks.draft", "");
  const [query, setQuery] = usePageState("drinks.query", "");
  const [required, setRequired] = usePageState<string[]>("drinks.with", [], isStringList);
  const [alcohol, setAlcohol] = usePageState<Alcohol>("drinks.alcohol", "any", isAlcohol);
  const [category, setCategory] = usePageState<string | null>(
    "drinks.category",
    null,
    isStringOrNull,
  );
  const body = cocktailSearchBody({ query, ingredients: required, alcohol, category }, tastes);
  const results = useCocktailSearch(body);

  return (
    <div className={browse.page}>
      <PageHeader title="Cocktails" documentTitle="Search recipes · Cocktails" intro={intro} />
      <form
        className={browse.panel}
        aria-label="Find a recipe"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(draft);
        }}
      >
        <div className={browse.panelHead}>
          <h2 className={browse.panelTitle}>Find a recipe</h2>
        </div>
        <div className={browse.searchRow}>
          <Field label="Describe a drink">
            {({ id }) => (
              <TextInput
                id={id}
                search
                value={draft}
                maxLength={200}
                placeholder="e.g. fresh and minty, with lime"
                autoComplete="off"
                enterKeyHint="search"
                onChange={(event) => {
                  setDraft(event.target.value);
                }}
              />
            )}
          </Field>
          <Button type="submit" variant="primary" icon={<Search />} className={styles.submit}>
            Find drinks
          </Button>
        </div>
        <div className={styles.filters}>
          <Combobox
            label="Must include"
            options={ingredients.data ?? []}
            value={required}
            onChange={setRequired}
            max={LIMITS.ingredients}
            placeholder={ingredients.isPending ? "Loading ingredients…" : "Any ingredients"}
          />
          <AlcoholFilter value={alcohol} onChange={setAlcohol} />
          <Field label="Category">
            {({ id }) => (
              <Select
                id={id}
                value={category ?? ""}
                onChange={(event) => {
                  setCategory(event.target.value || null);
                }}
              >
                <option value="">All</option>
                {(categories.data ?? []).map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>
        <TastesNote tastes={tastes} />
        <Examples
          examples={EXAMPLES}
          current={query}
          onPick={(example) => {
            setDraft(example);
            setQuery(example);
          }}
        />
      </form>
      <Results
        title="Matching drinks"
        query={results}
        enabled={body !== null}
        kind="cocktail"
        noun="drink"
        idle="Describe a drink, pick an ingredient or a category, or try an example."
        empty="No matching drinks. Try fewer ingredients or another category."
      >
        {(hits) => (
          <ItemGrid label="Matching drinks" kind="cocktail">
            {hits.map((hit) => (
              <CocktailCard
                key={hit.cocktail.drink_id}
                cocktail={hit.cocktail}
                reason={hit.reasons?.[0]}
                detailed
              />
            ))}
          </ItemGrid>
        )}
      </Results>
    </div>
  );
}
