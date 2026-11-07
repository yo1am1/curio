import { Ban, Heart, RefreshCw, Shirt, TrendingDown } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import { productImageUrl } from "../../api/client";
import type { Outfit } from "../../api/types";
import { budgetLine, euro, productName } from "../../domains/format";
import { Button } from "../../ui/Button";
import { Photo } from "../../ui/Photo";
import { useDetails } from "../details/DetailsContext";
import { cardVariants, gridVariants } from "./motion";
import styles from "./OutfitCard.module.css";
import { cheaperPrompt, SAVE_OUTFIT, swapPrompt } from "./prompts";

/** A fashion outfit in chat: one tile per slot, the total against the budget, and (on the
 * latest turn) Swap, Cheaper and Save outfit, which ask the agent. */
export function OutfitCard({
  outfit,
  active,
  send,
}: {
  outfit: Outfit;
  active: boolean;
  send: (message: string) => void;
}) {
  const details = useDetails();
  const share = outfit.budget ? Math.min(outfit.total / outfit.budget, 1) : 0;
  return (
    <section
      className={styles.outfit}
      aria-label={`Outfit for ${outfit.occasion}`}
      data-testid="outfit-card"
    >
      <header className={styles.head}>
        <Shirt size={17} aria-hidden="true" />
        <h3 className={styles.title}>Outfit · {outfit.occasion}</h3>
      </header>
      <motion.ul className={styles.slots} variants={gridVariants} initial="hidden" animate="shown">
        {outfit.slots.map((slot) => {
          const product = slot.product;
          return (
            <motion.li key={slot.slot} className={styles.slot} variants={cardVariants}>
              <p className={styles.slotLabel}>{slot.label}</p>
              <AnimatePresence mode="popLayout" initial={false}>
                <motion.div
                  key={product?.product_id ?? "gap"}
                  initial={{ opacity: 0, scale: 0.96 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.96 }}
                  transition={{ duration: 0.25 }}
                >
                  {product ? (
                    <button
                      type="button"
                      className={styles.piece}
                      aria-label={`View ${productName(product)}`}
                      onClick={() => {
                        details.open({ kind: "product", id: product.product_id });
                      }}
                    >
                      <Photo
                        sources={[productImageUrl(product.product_id)]}
                        alt=""
                        ratio="2 / 3"
                        paper
                      />
                      <span className={styles.name}>{productName(product)}</span>
                      <span className={styles.meta}>
                        <span className="tabular">{euro(product.price)}</span> · {product.colour}
                      </span>
                    </button>
                  ) : (
                    <p className={styles.gap}>
                      <Ban size={14} aria-hidden="true" /> {slot.issue}
                    </p>
                  )}
                </motion.div>
              </AnimatePresence>
              {product && slot.reason ? <p className={styles.reason}>{slot.reason}</p> : null}
              {product && active ? (
                <div className={styles.actions}>
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<RefreshCw />}
                    aria-label={`Ask Curio for another ${slot.label.toLowerCase()}`}
                    onClick={() => {
                      send(swapPrompt(slot.label));
                    }}
                  >
                    Swap
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<TrendingDown />}
                    aria-label={`Ask Curio for a cheaper ${slot.label.toLowerCase()}`}
                    onClick={() => {
                      send(cheaperPrompt(slot.label));
                    }}
                  >
                    Cheaper
                  </Button>
                </div>
              ) : null}
            </motion.li>
          );
        })}
      </motion.ul>
      <div className={styles.total}>
        {outfit.budget ? (
          <div className={styles.meter} aria-hidden="true">
            <motion.span
              className={styles.fill}
              initial={{ width: 0 }}
              animate={{ width: `${share * 100}%` }}
              transition={{ type: "spring", stiffness: 120, damping: 22 }}
            />
          </div>
        ) : null}
        <p className="tabular">{budgetLine(outfit.total, outfit.budget)}</p>
      </div>
      {outfit.notes?.map((note) => (
        <p key={note} className={styles.note}>
          {note}
        </p>
      ))}
      {active && outfit.slots.some((slot) => slot.product) ? (
        <Button
          variant="primary"
          icon={<Heart />}
          title="Curio proposes saving every piece; you approve or untick pieces."
          onClick={() => {
            send(SAVE_OUTFIT);
          }}
        >
          Save outfit
        </Button>
      ) : null}
    </section>
  );
}
