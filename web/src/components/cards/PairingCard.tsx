import { Heart, Wine } from "lucide-react";
import { motion } from "motion/react";

import type { BookPairing } from "../../api/types";
import { drinkPhoto } from "../../domains/format";
import { useSession } from "../../state/stores";
import { useSessionActions } from "../../state/actions";
import { Button } from "../../ui/Button";
import { Photo } from "../../ui/Photo";
import { useDetails } from "../details/DetailsContext";
import { AlcoholBadge } from "./ItemCards";
import { cardVariants, gridVariants } from "./motion";
import styles from "./PairingCard.module.css";

/** Drinks paired with a book (or a reading list): photo, alcohol status, the pairer's
 * reasons, the recipe and Save. */
export function PairingCard({
  result,
  compact = false,
}: {
  result: BookPairing;
  compact?: boolean;
}) {
  const shortlist = useSession((s) => s.cocktailShortlist);
  const saved = new Set(shortlist.map((c) => c.drink_id));
  const { saveCocktail } = useSessionActions();
  const details = useDetails();
  const free = result.profile.alcohol_free;
  return (
    <div className={`${styles.pairing} ${compact ? styles.compact : ""}`}>
      <p className={styles.summary}>{result.profile.summary}</p>
      {result.pairings.length === 0 ? (
        <p className={styles.empty}>
          {free
            ? "No alcohol-free recipe fits this profile yet. Try another book."
            : "No recipe fits this profile yet. Try another book."}
        </p>
      ) : (
        <motion.ul
          className={styles.drinks}
          variants={gridVariants}
          initial="hidden"
          animate="shown"
        >
          {result.pairings.map(({ cocktail, reasons }) => {
            const isSaved = saved.has(cocktail.drink_id);
            return (
              <motion.li
                key={cocktail.drink_id}
                className={styles.drink}
                variants={cardVariants}
                data-testid="pairing-drink"
              >
                <div className={styles.photo}>
                  <Photo
                    sources={[drinkPhoto(cocktail)]}
                    alt={cocktail.name}
                    ratio="4 / 3"
                    fit="cover"
                  />
                </div>
                <div className={styles.body}>
                  <h4 className={styles.name}>{cocktail.name}</h4>
                  <AlcoholBadge cocktail={cocktail} />
                  <ul className={styles.reasons}>
                    {reasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                  <div className={styles.actions}>
                    <Button
                      variant="ghost"
                      size="sm"
                      icon={<Wine />}
                      aria-label={`Open the ${cocktail.name} recipe`}
                      onClick={() => {
                        details.open({ kind: "cocktail", id: cocktail.drink_id });
                      }}
                    >
                      Recipe
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      icon={<Heart fill={isSaved ? "currentColor" : "none"} />}
                      disabled={isSaved}
                      aria-label={isSaved ? `${cocktail.name} is saved` : `Save ${cocktail.name}`}
                      onClick={() => {
                        saveCocktail(cocktail);
                      }}
                    >
                      {isSaved ? "Saved" : "Save"}
                    </Button>
                  </div>
                </div>
              </motion.li>
            );
          })}
        </motion.ul>
      )}
      <p className={styles.note}>{result.note}</p>
    </div>
  );
}
