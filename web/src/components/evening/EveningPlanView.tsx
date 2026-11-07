import {
  Ban,
  BookOpen,
  Heart,
  Info,
  Leaf,
  RefreshCw,
  Shirt,
  Sparkles,
  TrendingDown,
  Wine,
} from "lucide-react";
import { AnimatePresence, motion, type Variants } from "motion/react";
import type { ReactNode } from "react";

import { productImageUrl } from "../../api/client";
import type { EveningPlan } from "../../api/types";
import {
  budgetLine,
  capitalize,
  drinkPhoto,
  euro,
  isAlcoholFree,
  primaryAuthor,
  productName,
} from "../../domains/format";
import { Badge } from "../../ui/Badge";
import { Button } from "../../ui/Button";
import { Photo } from "../../ui/Photo";
import { AlcoholBadge, BookCover } from "../cards/ItemCards";
import { useDetails } from "../details/DetailsContext";
import { ThemeIcon } from "./ThemeIcon";
import styles from "./EveningPlanView.module.css";
import {
  ALCOHOL_FREE_DRINK,
  ANOTHER_BOOK,
  ANOTHER_DRINK,
  type Part,
  type PlanActions,
  SAVE_EVENING,
  saysWhy,
} from "./plan";

const planVariants: Variants = {
  hidden: {},
  shown: { transition: { staggerChildren: 0.12, delayChildren: 0.05 } },
};

const partVariants: Variants = {
  hidden: { opacity: 0, y: 26, scale: 0.98 },
  shown: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { type: "spring", stiffness: 260, damping: 28 },
  },
};

const swapMotion = {
  initial: { opacity: 0, y: 12, filter: "blur(4px)" },
  animate: { opacity: 1, y: 0, filter: "blur(0px)" },
  exit: { opacity: 0, y: -10, filter: "blur(4px)" },
  transition: { duration: 0.32, ease: [0.22, 1, 0.36, 1] as const },
};

function missingReason(plan: EveningPlan, part: Part): string {
  return plan.missing?.find((m) => m.part === part)?.reason ?? "";
}

function PartCard({
  part,
  icon,
  title,
  children,
}: {
  part: Part;
  icon: ReactNode;
  title: string;
  children: ReactNode;
}) {
  return (
    <motion.article
      className={`${styles.card} ${styles[part]}`}
      variants={partVariants}
      aria-labelledby={`evening-${part}-title`}
      data-testid={`evening-${part}`}
      layout="position"
    >
      <header className={styles.cardHead}>
        <span className={styles.cardIcon} aria-hidden="true">
          {icon}
        </span>
        <h3 id={`evening-${part}-title`} className={styles.cardTitle}>
          {title}
        </h3>
      </header>
      {children}
    </motion.article>
  );
}

function Missing({ reason }: { reason: string }) {
  return (
    <p className={styles.missing}>
      <Ban size={16} aria-hidden="true" />
      <span>{reason}</span>
    </p>
  );
}

function BudgetMeter({ total, budget }: { total: number; budget?: number | null }) {
  const share = budget ? Math.min(total / budget, 1) : 0;
  return (
    <div className={styles.budget}>
      {budget ? (
        <div
          className={styles.meter}
          role="progressbar"
          aria-label="Outfit budget used"
          aria-valuemin={0}
          aria-valuemax={budget}
          aria-valuenow={Math.min(total, budget)}
          data-over={total > budget ? "true" : undefined}
        >
          <motion.span
            className={styles.meterFill}
            initial={false}
            animate={{ width: `${share * 100}%` }}
            transition={{ type: "spring", stiffness: 140, damping: 24 }}
          />
        </div>
      ) : null}
      <p className="tabular">{budgetLine(total, budget)}</p>
    </div>
  );
}

function OutfitPart({ plan, actions }: { plan: EveningPlan; actions: PlanActions }) {
  const details = useDetails();
  const part = plan.outfit;
  if (!part) return <Missing reason={missingReason(plan, "outfit")} />;
  const outfit = part.outfit;
  const active = actions.kind === "page" || actions.active;
  return (
    <>
      <ul className={styles.pieces}>
        {outfit.slots.map((slot) => {
          const product = slot.product;
          const label = slot.label.toLowerCase();
          const busy = actions.kind === "page" && actions.busy === `outfit:${slot.slot}`;
          return (
            <li key={slot.slot} className={styles.piece} data-slot={slot.slot}>
              <AnimatePresence mode="popLayout" initial={false}>
                <motion.div
                  key={product?.product_id ?? `gap-${slot.slot}`}
                  className={styles.pieceRow}
                  {...swapMotion}
                >
                  {product ? (
                    <>
                      <button
                        type="button"
                        className={styles.pieceThumb}
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
                      </button>
                      <div className={styles.pieceText}>
                        <p className={styles.pieceName}>{productName(product)}</p>
                        <p className={styles.pieceMeta}>
                          {slot.label} · <span className="tabular">{euro(product.price)}</span> ·{" "}
                          {product.colour}
                        </p>
                        {active ? (
                          <div className={styles.pieceActions}>
                            <Button
                              variant="ghost"
                              size="sm"
                              icon={<RefreshCw className={busy ? styles.spin : undefined} />}
                              title={`Another ${label}`}
                              aria-label={`Swap the ${label}`}
                              disabled={actions.kind === "page" && !!actions.busy}
                              onClick={() => {
                                if (actions.kind === "page") actions.swap("outfit", slot.slot);
                                else actions.send(`Another ${label} for my evening`);
                              }}
                            >
                              Swap
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              icon={<TrendingDown />}
                              title={`A cheaper ${label}`}
                              aria-label={`A cheaper ${label}`}
                              disabled={actions.kind === "page" && !!actions.busy}
                              onClick={() => {
                                if (actions.kind === "page")
                                  actions.swap("outfit", slot.slot, "cheaper");
                                else actions.send(`Cheaper ${label}`);
                              }}
                            >
                              Cheaper
                            </Button>
                          </div>
                        ) : null}
                      </div>
                    </>
                  ) : (
                    <p className={styles.pieceGap}>
                      <strong>{slot.label}</strong>: {slot.issue}
                    </p>
                  )}
                </motion.div>
              </AnimatePresence>
            </li>
          );
        })}
      </ul>
      <BudgetMeter total={outfit.total} budget={outfit.budget} />
      <p className={styles.reason}>{part.reason}</p>
      {outfit.notes?.map((note) => (
        <p key={note} className={styles.note}>
          <Info size={14} aria-hidden="true" /> {note}
        </p>
      ))}
    </>
  );
}

function BookPart({ plan, actions }: { plan: EveningPlan; actions: PlanActions }) {
  const details = useDetails();
  const part = plan.book;
  if (!part) return <Missing reason={missingReason(plan, "book")} />;
  const book = part.book;
  const active = actions.kind === "page" || actions.active;
  const busy = actions.kind === "page" && actions.busy === "book";
  return (
    <>
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.div key={book.book_id} className={styles.feature} {...swapMotion}>
          <button
            type="button"
            className={styles.featureMedia}
            aria-label={`View ${book.title}`}
            onClick={() => {
              details.open({ kind: "book", id: book.book_id });
            }}
          >
            <BookCover book={book} large />
          </button>
          <div className={styles.featureText}>
            <p className={styles.featureName}>{book.title}</p>
            <p className={styles.featureMeta}>
              {[primaryAuthor(book), ...(book.genres ?? []).slice(0, 2)].join(" · ")}
            </p>
            <p className={styles.reason}>{part.reason}</p>
          </div>
        </motion.div>
      </AnimatePresence>
      {active ? (
        <div className={styles.partActions}>
          <Button
            variant="secondary"
            size="sm"
            icon={<RefreshCw className={busy ? styles.spin : undefined} />}
            disabled={actions.kind === "page" && !!actions.busy}
            onClick={() => {
              if (actions.kind === "page") actions.swap("book");
              else actions.send(ANOTHER_BOOK);
            }}
          >
            {ANOTHER_BOOK}
          </Button>
        </div>
      ) : null}
    </>
  );
}

function DrinkPart({
  plan,
  actions,
  note,
}: {
  plan: EveningPlan;
  actions: PlanActions;
  note: boolean;
}) {
  const details = useDetails();
  const part = plan.drink;
  if (!part) return <Missing reason={missingReason(plan, "drink")} />;
  const drink = part.cocktail;
  const active = actions.kind === "page" || actions.active;
  const busy = actions.kind === "page" && actions.busy === "drink";
  const free = isAlcoholFree(drink);
  return (
    <>
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.div key={drink.drink_id} className={styles.feature} {...swapMotion}>
          <button
            type="button"
            className={styles.featureMedia}
            aria-label={`View ${drink.name}`}
            onClick={() => {
              details.open({ kind: "cocktail", id: drink.drink_id });
            }}
          >
            <Photo sources={[drinkPhoto(drink)]} alt="" ratio="1" fit="cover" />
          </button>
          <div className={styles.featureText}>
            <p className={styles.featureName}>{drink.name}</p>
            <AlcoholBadge cocktail={drink} />
            {part.reasons[0] ? <p className={styles.reason}>{part.reasons[0]}</p> : null}
            {part.evening_fit?.length ? (
              <p className={styles.reason}>Suits the evening: {part.evening_fit.join(", ")}</p>
            ) : null}
          </div>
        </motion.div>
      </AnimatePresence>
      {active ? (
        <div className={styles.partActions}>
          <Button
            variant="secondary"
            size="sm"
            icon={<RefreshCw className={busy ? styles.spin : undefined} />}
            disabled={actions.kind === "page" && !!actions.busy}
            onClick={() => {
              if (actions.kind === "page") actions.swap("drink");
              else actions.send(ANOTHER_DRINK);
            }}
          >
            {ANOTHER_DRINK}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            icon={<Leaf />}
            disabled={free || (actions.kind === "page" && !!actions.busy)}
            title="An alcohol-free drink paired with the same book"
            onClick={() => {
              if (actions.kind === "page") actions.swap("drink", undefined, "alcohol_free");
              else actions.send(ALCOHOL_FREE_DRINK);
            }}
          >
            Alcohol-free
          </Button>
        </div>
      ) : null}
      {note ? <p className={styles.note}>{part.note}</p> : null}
    </>
  );
}

/** The plan: its theme, three cards (outfit, book, drink), why it works and Save evening.
 * `said` is the chat answer the plan comes with: what it already says is not repeated. */
export function EveningPlanView({
  plan,
  actions,
  said = "",
  planKey,
}: {
  plan: EveningPlan;
  actions: PlanActions;
  said?: string;
  /** Changes when a new plan (not a swap) arrives, to replay the entrance. */
  planKey?: string;
}) {
  const theme = plan.theme;
  const evidence = `${capitalize(theme.evidence)}, so ${theme.feeling}.`;
  const showWhy = !saysWhy(said, plan);
  const note = !said.toLowerCase().includes("responsibl");
  const chat = actions.kind === "chat";
  const active = actions.kind === "page" || actions.active;
  return (
    <motion.div
      key={planKey}
      className={`${styles.plan} ${chat ? styles.inChat : ""}`}
      variants={planVariants}
      initial="hidden"
      animate="shown"
      data-testid="evening-plan"
    >
      <motion.div className={styles.theme} variants={partVariants}>
        <Badge tone="violet" icon={<ThemeIcon name={theme.icon} />} className={styles.themeBadge}>
          {theme.label}
        </Badge>
        <p className={styles.evidence}>{evidence}</p>
      </motion.div>
      {showWhy ? (
        <motion.div className={styles.why} variants={partVariants}>
          <Sparkles size={18} aria-hidden="true" className={styles.whyIcon} />
          <p>
            <strong>Why it works</strong> · {plan.why}
          </p>
        </motion.div>
      ) : null}
      <div className={styles.parts}>
        <PartCard part="outfit" icon={<Shirt size={17} />} title="Outfit">
          <OutfitPart plan={plan} actions={actions} />
        </PartCard>
        <PartCard part="book" icon={<BookOpen size={17} />} title="Book">
          <BookPart plan={plan} actions={actions} />
        </PartCard>
        <PartCard part="drink" icon={<Wine size={17} />} title="Drink">
          <DrinkPart plan={plan} actions={actions} note={note} />
        </PartCard>
      </div>
      {plan.notes?.map((text) => (
        <p key={text} className={styles.note}>
          <Info size={14} aria-hidden="true" /> {text}
        </p>
      ))}
      {active && plan.status !== "empty" ? (
        <motion.div className={styles.save} variants={partVariants}>
          <Button
            variant="primary"
            size="lg"
            icon={<Heart />}
            title={
              chat
                ? "Curio proposes saving every part; you approve or untick parts."
                : "Save the pieces, the book and the drink to your lists, after you approve."
            }
            onClick={() => {
              if (actions.kind === "page") actions.save();
              else actions.send(SAVE_EVENING);
            }}
          >
            Save evening
          </Button>
        </motion.div>
      ) : null}
    </motion.div>
  );
}
