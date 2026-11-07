import { Check, Plus, Sparkles } from "lucide-react";
import { motion } from "motion/react";

import type { PantryMatch } from "../../api/types";
import styles from "./Cocktails.module.css";

/** How much of a recipe the pantry covers: a meter, what you have and what is missing. */
export function PantryCoverage({
  match,
}: {
  match: Pick<PantryMatch, "have" | "missing" | "coverage">;
}) {
  const total = match.have.length + match.missing.length;
  const label = `You have ${String(match.have.length)} of ${String(total)} ingredients`;
  const complete = match.missing.length === 0;
  return (
    <div className={styles.coverage} data-testid="pantry-coverage">
      <div className={styles.meterHead}>
        <span>{label}</span>
        <span className="tabular">{Math.round(match.coverage * 100)}%</span>
      </div>
      <div
        className={styles.meter}
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(match.coverage * 100)}
        data-complete={complete ? "true" : undefined}
      >
        <motion.span
          className={styles.meterFill}
          initial={{ width: 0 }}
          animate={{ width: `${String(match.coverage * 100)}%` }}
          transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
        />
      </div>
      <ul className={styles.ingredientChips} aria-label="Ingredients you have">
        {match.have.map((name) => (
          <li key={name} className={styles.have}>
            <Check size={12} aria-hidden="true" />
            {name}
          </li>
        ))}
      </ul>
      {complete ? (
        <p className={styles.ready}>
          <Sparkles size={14} aria-hidden="true" /> You can make this now.
        </p>
      ) : (
        <ul className={styles.ingredientChips} aria-label="Missing ingredients">
          {match.missing.map((name) => (
            <li key={name} className={styles.missing}>
              <Plus size={12} aria-hidden="true" />
              {name}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
