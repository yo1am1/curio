import { Columns3, MessageCircle, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useId, type ReactNode } from "react";

import { productImageUrl } from "../../api/client";
import { useDemoCustomers } from "../../api/queries";
import type { Domain } from "../../api/types";
import { DOMAINS, type PolicySetting } from "../../domains/registry";
import {
  alcoholLabel,
  coverSources,
  drinkPhoto,
  euro,
  primaryAuthor,
  productName,
} from "../../domains/format";
import { useSession } from "../../state/stores";
import { useSessionActions } from "../../state/actions";
import { memoryChips } from "../../state/session";
import { Button } from "../../ui/Button";
import { Photo } from "../../ui/Photo";
import { Toggle } from "../../ui/Toggle";
import { useDetails } from "../details/DetailsContext";
import styles from "./SessionPanel.module.css";

function Section({
  title,
  children,
  count,
}: {
  title: string;
  children: ReactNode;
  count?: number;
}) {
  const id = useId();
  return (
    <section className={styles.section} aria-labelledby={id}>
      <h3 id={id} className={styles.sectionTitle}>
        {title}
        {count !== undefined ? (
          <span className={styles.count}>
            {" · "}
            <motion.span key={count} initial={{ y: -6, opacity: 0 }} animate={{ y: 0, opacity: 1 }}>
              {count}
            </motion.span>
          </span>
        ) : null}
      </h3>
      {children}
    </section>
  );
}

interface Row {
  id: number;
  name: string;
  meta: string;
  photo: (string | null | undefined)[];
  ratio: string;
  fit: "contain" | "cover";
  paper?: boolean;
  open: () => void;
  remove: () => void;
}

function SavedList({ rows, empty, label }: { rows: Row[]; empty: string; label: string }) {
  if (!rows.length) return <p className={styles.hint}>{empty}</p>;
  return (
    <ul className={styles.saved} aria-label={label}>
      <AnimatePresence initial={false}>
        {rows.map((row) => (
          <motion.li
            key={row.id}
            layout
            className={styles.row}
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.22 }}
          >
            <button type="button" className={styles.rowMain} onClick={row.open}>
              <span className={styles.rowThumb}>
                <Photo
                  sources={row.photo}
                  alt=""
                  ratio={row.ratio}
                  fit={row.fit}
                  paper={row.paper}
                  placeholder=""
                />
              </span>
              <span className={styles.rowText}>
                <span className={styles.rowName}>{row.name}</span>
                <span className={styles.rowMeta}>{row.meta}</span>
              </span>
            </button>
            <Button
              variant="ghost"
              size="sm"
              icon={<X />}
              aria-label={`Remove ${row.name}`}
              title="Remove"
              onClick={row.remove}
            />
          </motion.li>
        ))}
      </AnimatePresence>
    </ul>
  );
}

function Approvals({ domain }: { domain: Domain }) {
  const policy = useSession((s) => s.policy);
  const { setPolicy } = useSessionActions();
  const view = DOMAINS[domain];
  const settings = (Object.keys(view.approvalTools) as PolicySetting[]).map((setting) => ({
    setting,
    label: setting === "ask_shortlist" ? view.copy.askShortlist : view.copy.askMemory,
  }));
  return (
    <Section title="Approvals">
      <div className={styles.toggles}>
        {settings.map(({ setting, label }) => (
          <Toggle
            key={setting}
            checked={policy[setting]}
            onChange={(ask) => {
              setPolicy(setting, ask);
            }}
          >
            {label}
          </Toggle>
        ))}
      </div>
    </Section>
  );
}

function Memory({ domain }: { domain: Domain }) {
  const session = useSession((s) => s);
  const { forget } = useSessionActions();
  const chips = memoryChips(domain, session);
  return (
    <Section title="Memory">
      {chips.length ? (
        <ul className={styles.chips} aria-label="Remembered for this session">
          <AnimatePresence initial={false}>
            {chips.map((chip) => (
              <motion.li
                key={`${chip.memory}-${chip.field}-${String(chip.value)}`}
                layout
                initial={{ opacity: 0, scale: 0.8 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.8 }}
              >
                <button
                  type="button"
                  className={styles.chip}
                  aria-label={`Forget “${chip.label}”`}
                  title="Forget this"
                  onClick={() => {
                    forget(chip);
                  }}
                >
                  <span>{chip.label}</span>
                  <X size={14} aria-hidden="true" />
                </button>
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      ) : (
        <p className={styles.hint}>{DOMAINS[domain].copy.memoryHint}</p>
      )}
      {domain === "cocktails" ? (
        <p className={styles.fine}>
          Avoided ingredients filter listed recipe ingredients only, not allergens.
        </p>
      ) : null}
    </Section>
  );
}

function ShopperLink() {
  const shopper = useSession((s) => s.shopperId);
  const { setShopper } = useSessionActions();
  const { data } = useDemoCustomers(true);
  const id = useId();
  const examples = (data ?? []).slice(0, 10);
  return (
    <div className={styles.field}>
      <label htmlFor={id} className={styles.fieldLabel}>
        Personalize with a shopping history
      </label>
      <select
        id={id}
        className={styles.select}
        value={shopper ?? ""}
        onChange={(event) => {
          setShopper(event.target.value ? Number(event.target.value) : null);
        }}
      >
        <option value="">Not linked</option>
        {examples.map((example) => (
          <option key={example.user_id} value={example.user_id}>
            {example.label} (#{example.user_id})
          </option>
        ))}
        {shopper != null && !examples.some((e) => e.user_id === shopper) ? (
          <option value={shopper}>Shopper #{shopper}</option>
        ) : null}
      </select>
      <p className={styles.fine}>Linked histories unlock “Picks from my shopping history”.</p>
    </div>
  );
}

/** Client-held agent state for the domain: approvals, memory and the saved lists. */
export function SessionPanel({
  domain,
  onAsk,
  embedded = false,
  onLeave,
}: {
  domain: Domain;
  onAsk: (message: string) => void;
  /** Inside another surface (the session sheet): no card chrome or title of its own. */
  embedded?: boolean;
  /** Called before the panel opens a dialog or hands off to chat (the sheet closes). */
  onLeave?: () => void;
}) {
  const shortlist = useSession((s) => s.shortlist);
  const drinks = useSession((s) => s.cocktailShortlist);
  const books = useSession((s) => s.readingList);
  const actions = useSessionActions();
  const shown = useDetails();
  const details = {
    open: (target: Parameters<typeof shown.open>[0]) => {
      onLeave?.();
      shown.open(target);
    },
  };
  const copy = DOMAINS[domain].copy;
  const saved =
    domain === "fashion"
      ? shortlist
      : domain === "cocktails"
        ? drinks
        : domain === "books"
          ? books
          : [];

  const productRows: Row[] = shortlist.map((p) => ({
    id: p.product_id,
    name: productName(p),
    meta: euro(p.price),
    photo: [productImageUrl(p.product_id)],
    ratio: "2 / 3",
    fit: "contain",
    paper: true,
    open: () => {
      details.open({ kind: "product", id: p.product_id });
    },
    remove: () => {
      actions.removeProduct(p.product_id);
    },
  }));
  const drinkRows: Row[] = drinks.map((c) => ({
    id: c.drink_id,
    name: c.name,
    meta: alcoholLabel(c),
    photo: [drinkPhoto(c)],
    ratio: "1",
    fit: "cover",
    open: () => {
      details.open({ kind: "cocktail", id: c.drink_id });
    },
    remove: () => {
      actions.removeCocktail(c.drink_id);
    },
  }));
  const bookRows: Row[] = books.map((b) => ({
    id: b.book_id,
    name: b.title,
    meta: primaryAuthor(b),
    photo: coverSources(b).map((s) => s.url),
    ratio: "2 / 3",
    fit: "cover",
    open: () => {
      details.open({ kind: "book", id: b.book_id });
    },
    remove: () => {
      actions.removeBook(b.book_id);
    },
  }));

  return (
    <aside
      className={`${styles.panel} ${embedded ? styles.embedded : ""}`}
      aria-label="Your session"
      data-testid="session-panel"
    >
      {embedded ? null : <h2 className={styles.title}>Your session</h2>}
      {domain === "fashion" ? <ShopperLink /> : null}
      <Approvals domain={domain} />
      <Memory domain={domain} />
      {domain === "fashion" ? (
        <Section title="Shortlist" count={shortlist.length}>
          <SavedList rows={productRows} empty={copy.shortlistHint} label="Saved pieces" />
        </Section>
      ) : null}
      {domain === "cocktails" ? (
        <Section title="Shortlist" count={drinks.length}>
          <SavedList rows={drinkRows} empty={copy.shortlistHint} label="Saved drinks" />
        </Section>
      ) : null}
      {domain === "books" ? (
        <Section title="Reading list" count={books.length}>
          <SavedList rows={bookRows} empty={copy.shortlistHint} label="Your reading list" />
        </Section>
      ) : null}
      {domain === "evening" ? (
        <Section title="Saved">
          <dl className={styles.counts}>
            <div>
              <dt>Pieces</dt>
              <dd>{shortlist.length}</dd>
            </div>
            <div>
              <dt>Books</dt>
              <dd>{books.length}</dd>
            </div>
            <div>
              <dt>Drinks</dt>
              <dd>{drinks.length}</dd>
            </div>
          </dl>
          <p className={styles.hint}>{copy.shortlistHint}</p>
        </Section>
      ) : null}
      {saved.length >= 2 ? (
        <div className={styles.listActions}>
          {domain === "fashion" || domain === "cocktails" ? (
            <Button
              icon={<Columns3 />}
              size="sm"
              onClick={() => {
                details.open({ kind: "compare", domain });
              }}
            >
              Compare
            </Button>
          ) : null}
          <Button
            icon={<MessageCircle />}
            size="sm"
            variant={domain === "books" ? "secondary" : "ghost"}
            onClick={() => {
              onLeave?.();
              onAsk(copy.comparePrompt);
            }}
          >
            {domain === "books" ? "Ask Curio which to read first" : "Ask Curio"}
          </Button>
        </div>
      ) : null}
    </aside>
  );
}
