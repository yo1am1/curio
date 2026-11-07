import { Heart, MessageCircle } from "lucide-react";
import { useEffect } from "react";

import { useCocktail, useSimilarCocktails } from "../../api/queries";
import { RECIPE_CREDIT } from "../../domains/registry";
import { alcoholLabel, drinkPhoto } from "../../domains/format";
import { useSession } from "../../state/stores";
import { useSessionActions } from "../../state/actions";
import { Button } from "../../ui/Button";
import { Photo } from "../../ui/Photo";
import { AlcoholBadge } from "../cards/ItemCards";
import { useDetails } from "./DetailsContext";
import styles from "./Details.module.css";
import { DetailError, DetailSkeleton } from "./states";
import { useAskCurio } from "./useAskCurio";

export function CocktailDetails({ id, onTitle }: { id: number; onTitle: (title: string) => void }) {
  const { data: cocktail, error, isPending } = useCocktail(id);
  const saved = useSession((s) => s.cocktailShortlist.some((c) => c.drink_id === id));
  const { saveCocktail } = useSessionActions();
  const ask = useAskCurio("cocktails");
  useEffect(() => {
    if (cocktail) onTitle(cocktail.name);
  }, [cocktail, onTitle]);

  if (isPending) return <DetailSkeleton ratio="1" />;
  if (error) return <DetailError error={error} />;

  return (
    <>
      <div className={styles.layout}>
        <div className={styles.media}>
          <Photo sources={[drinkPhoto(cocktail)]} alt={cocktail.name} ratio="1" fit="cover" />
        </div>
        <div className={styles.info}>
          <p className={styles.kicker}>{cocktail.category}</p>
          <h2 className={styles.name}>{cocktail.name}</h2>
          <div className={styles.chips}>
            <AlcoholBadge cocktail={cocktail} />
            <span className={styles.facts}>Served in a {cocktail.glass.toLowerCase()}</span>
          </div>
          <h3 className={styles.sectionTitle}>Ingredients</h3>
          <ul className={styles.ingredients}>
            {cocktail.ingredients.map((item, index) => (
              <li key={`${item.name}-${index}`}>
                <span>{item.name}</span>
                <span className={styles.measure}>{(item.measure ?? "").trim()}</span>
              </li>
            ))}
          </ul>
          <h3 className={styles.sectionTitle}>Method</h3>
          <p className={styles.text}>{cocktail.instructions || "No instructions are listed."}</p>
          <div className={styles.actions}>
            <Button
              variant={saved ? "secondary" : "primary"}
              icon={<Heart fill={saved ? "currentColor" : "none"} />}
              disabled={saved}
              onClick={() => {
                saveCocktail(cocktail);
              }}
              data-autofocus
            >
              {saved ? "Saved" : "Save to shortlist"}
            </Button>
            {ask ? (
              <Button
                icon={<MessageCircle />}
                onClick={() => {
                  ask(`Tell me about the ${cocktail.name} (drink ${id}).`);
                }}
              >
                Ask Curio about it
              </Button>
            ) : null}
          </div>
          <p className={styles.note}>{RECIPE_CREDIT}</p>
        </div>
      </div>
      <SimilarDrinks id={id} />
    </>
  );
}

function SimilarDrinks({ id }: { id: number }) {
  const { data, isPending } = useSimilarCocktails(id);
  const details = useDetails();
  return (
    <section className={styles.section} aria-labelledby={`similar-${id}`}>
      <h3 id={`similar-${id}`} className={styles.sectionTitle}>
        Similar drinks
      </h3>
      {isPending ? <div className={styles.skeleton} style={{ height: "8rem" }} /> : null}
      {!isPending && !data?.length ? (
        <p className={styles.empty}>No similar drinks are available yet.</p>
      ) : null}
      {data?.length ? (
        <ul className={styles.related}>
          {data.map(({ cocktail, reasons }) => (
            <li key={cocktail.drink_id}>
              <button
                type="button"
                className={styles.relatedItem}
                onClick={() => {
                  details.open({ kind: "cocktail", id: cocktail.drink_id });
                }}
              >
                <span className={styles.relatedThumb}>
                  <Photo sources={[drinkPhoto(cocktail)]} alt="" ratio="1" fit="cover" />
                </span>
                <span>
                  <span className={styles.relatedName}>{cocktail.name}</span>
                  <br />
                  <span className={styles.relatedMeta}>
                    {alcoholLabel(cocktail)}
                    {reasons?.[0] ? ` · ${reasons[0]}` : ""}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
