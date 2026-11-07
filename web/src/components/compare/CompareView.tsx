import { Check, MessageCircle } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";

import { productImageUrl } from "../../api/client";
import type { Cocktail, Product } from "../../api/types";
import { DOMAINS, RECIPE_CREDIT } from "../../domains/registry";
import { drinkPhoto, euro, productName } from "../../domains/format";
import { useSession } from "../../state/stores";
import { Badge } from "../../ui/Badge";
import { Button } from "../../ui/Button";
import { Photo } from "../../ui/Photo";
import { StateMessage } from "../../ui/StateMessage";
import { AlcoholBadge } from "../cards/ItemCards";
import { cardVariants, gridVariants } from "../cards/motion";
import { useDetails } from "../details/DetailsContext";
import { useAskCurio } from "../details/useAskCurio";
import {
  MAX_COMPARE,
  cheapest,
  initialSelection,
  isShared,
  sharedIngredients,
  toggleSelection,
} from "./compare";
import styles from "./Compare.module.css";

export type CompareDomain = "fashion" | "cocktails";

function Picker<T>({
  items,
  id,
  name,
  selected,
  onToggle,
}: {
  items: T[];
  id: (item: T) => number;
  name: (item: T) => string;
  selected: number[];
  onToggle: (id: number) => void;
}) {
  if (items.length <= MAX_COMPARE) return null;
  return (
    <fieldset className={styles.picker}>
      <legend className={styles.pickerLegend}>
        Choose up to {MAX_COMPARE} · {selected.length} chosen
      </legend>
      <div className={styles.pickerChips}>
        {items.map((item) => {
          const on = selected.includes(id(item));
          return (
            <button
              key={id(item)}
              type="button"
              className={styles.pick}
              aria-pressed={on}
              disabled={!on && selected.length >= MAX_COMPARE}
              onClick={() => {
                onToggle(id(item));
              }}
            >
              {on ? <Check size={14} aria-hidden="true" /> : null}
              {name(item)}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

function Columns({ children, count }: { children: ReactNode; count: number }) {
  return (
    <motion.ul
      className={styles.columns}
      style={{ "--columns": count } as CSSProperties}
      variants={gridVariants}
      initial="hidden"
      animate="shown"
    >
      {children}
    </motion.ul>
  );
}

function ComparePieces({ items }: { items: Product[] }) {
  const ids = items.map((p) => p.product_id);
  const [selected, setSelected] = useState(() => initialSelection(ids));
  const details = useDetails();
  const shown = items.filter((p) => selected.includes(p.product_id));
  const low = cheapest(shown);
  return (
    <>
      <Picker
        items={items}
        id={(p) => p.product_id}
        name={productName}
        selected={selected}
        onToggle={(id) => {
          setSelected(toggleSelection(ids, selected, id));
        }}
      />
      {shown.length < 2 ? (
        <StateMessage tone="hint">Choose at least two pieces to compare them.</StateMessage>
      ) : (
        <Columns count={shown.length}>
          {shown.map((product) => {
            const name = productName(product);
            const facts = [product.product_type, product.colour, product.section].filter(Boolean);
            return (
              <motion.li
                key={product.product_id}
                className={styles.column}
                variants={cardVariants}
                data-testid="compare-piece"
              >
                <button
                  type="button"
                  className={styles.photoButton}
                  aria-label={`View ${name}`}
                  onClick={() => {
                    details.open({ kind: "product", id: product.product_id });
                  }}
                >
                  <Photo
                    sources={[productImageUrl(product.product_id)]}
                    alt={name}
                    ratio="2 / 3"
                    paper
                  />
                </button>
                <h3 className={styles.name}>{name}</h3>
                <div className={styles.priceRow}>
                  <strong className="tabular">{euro(product.price)}</strong>
                  {low.has(product.product_id) ? <Badge tone="green">Lowest price</Badge> : null}
                </div>
                <p className={styles.facts}>{facts.join(" · ")}</p>
                {product.description ? (
                  <p className={styles.description}>{product.description}</p>
                ) : null}
              </motion.li>
            );
          })}
        </Columns>
      )}
      <p className={styles.note}>Prices are approximate; current stock is unknown.</p>
    </>
  );
}

function CompareDrinks({ items }: { items: Cocktail[] }) {
  const ids = items.map((c) => c.drink_id);
  const [selected, setSelected] = useState(() => initialSelection(ids));
  const details = useDetails();
  const shown = items.filter((c) => selected.includes(c.drink_id));
  const shared = sharedIngredients(shown);
  return (
    <>
      <Picker
        items={items}
        id={(c) => c.drink_id}
        name={(c) => c.name}
        selected={selected}
        onToggle={(id) => {
          setSelected(toggleSelection(ids, selected, id));
        }}
      />
      {shown.length < 2 ? (
        <StateMessage tone="hint">Choose at least two drinks to compare them.</StateMessage>
      ) : (
        <>
          {shared.length ? (
            <p className={styles.shared} data-testid="shared-ingredients">
              <span className={styles.sharedLabel}>In every recipe</span>
              {shared.map((name) => (
                <span key={name} className={styles.sharedChip}>
                  {name}
                </span>
              ))}
            </p>
          ) : (
            <p className={styles.note}>These recipes share no ingredient.</p>
          )}
          <Columns count={shown.length}>
            {shown.map((cocktail) => (
              <motion.li
                key={cocktail.drink_id}
                className={styles.column}
                variants={cardVariants}
                data-testid="compare-drink"
              >
                <button
                  type="button"
                  className={styles.photoButton}
                  aria-label={`View ${cocktail.name}`}
                  onClick={() => {
                    details.open({ kind: "cocktail", id: cocktail.drink_id });
                  }}
                >
                  <Photo
                    sources={[drinkPhoto(cocktail)]}
                    alt={cocktail.name}
                    ratio="1"
                    fit="cover"
                  />
                </button>
                <h3 className={styles.name}>{cocktail.name}</h3>
                <div className={styles.priceRow}>
                  <AlcoholBadge cocktail={cocktail} />
                </div>
                <p className={styles.facts}>
                  {cocktail.category} · {cocktail.glass}
                </p>
                <ul className={styles.ingredients} aria-label={`${cocktail.name} ingredients`}>
                  {cocktail.ingredients.map((item, index) => {
                    const common = isShared(item.name, shared);
                    return (
                      <li
                        key={`${item.name}-${String(index)}`}
                        data-shared={common ? "true" : undefined}
                      >
                        {item.measure?.trim() ? (
                          <span className={styles.measure}>{item.measure.trim()} </span>
                        ) : null}
                        {item.name}
                        {common ? (
                          <span className="visually-hidden"> (in every recipe)</span>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              </motion.li>
            ))}
          </Columns>
        </>
      )}
      <p className={styles.note}>{RECIPE_CREDIT}</p>
    </>
  );
}

/** Saved pieces or drinks side by side (opened from the session panel). */
export function CompareView({
  domain,
  onTitle,
}: {
  domain: CompareDomain;
  onTitle?: (title: string) => void;
}) {
  const pieces = useSession((s) => s.shortlist);
  const drinks = useSession((s) => s.cocktailShortlist);
  const ask = useAskCurio(domain);
  const title = domain === "fashion" ? "Compare pieces" : "Compare drinks";
  useEffect(() => {
    onTitle?.(title);
  }, [onTitle, title]);
  const count = domain === "fashion" ? pieces.length : drinks.length;
  return (
    <div className={styles.compare} data-testid="compare">
      <div className={styles.head}>
        <h2 className={styles.title}>{title}</h2>
        {ask && count >= 2 ? (
          <Button
            size="sm"
            icon={<MessageCircle />}
            onClick={() => {
              ask(DOMAINS[domain].copy.comparePrompt);
            }}
          >
            Ask Curio which to {domain === "fashion" ? "pick" : "make"}
          </Button>
        ) : null}
      </div>
      {count < 2 ? (
        <StateMessage tone="hint">
          Save at least two {domain === "fashion" ? "pieces" : "drinks"} to compare them.
        </StateMessage>
      ) : domain === "fashion" ? (
        <ComparePieces items={pieces} />
      ) : (
        <CompareDrinks items={drinks} />
      )}
    </div>
  );
}
