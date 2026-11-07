import { Bookmark, Heart, Star } from "lucide-react";
import { motion } from "motion/react";
import type { ReactNode } from "react";

import { productImageUrl } from "../../api/client";
import type { Book, Cocktail, Product } from "../../api/types";
import {
  alcoholStatus,
  coverSources,
  drinkPhoto,
  euro,
  primaryAuthor,
  productName,
  rating,
} from "../../domains/format";
import { useSession } from "../../state/stores";
import { useSessionActions } from "../../state/actions";
import { Badge } from "../../ui/Badge";
import { Photo } from "../../ui/Photo";
import { useDetails } from "../details/DetailsContext";
import styles from "./ItemCards.module.css";
import { cardVariants, gridVariants } from "./motion";

export function ItemGrid({
  children,
  label,
  kind = "product",
}: {
  children: ReactNode;
  label: string;
  kind?: "product" | "cocktail" | "book";
}) {
  return (
    <motion.ul
      className={`${styles.grid} ${styles[`${kind}Grid`]}`}
      aria-label={label}
      variants={gridVariants}
      initial="hidden"
      animate="shown"
    >
      {children}
    </motion.ul>
  );
}

function SaveButton({
  saved,
  name,
  onSave,
  kind,
}: {
  saved: boolean;
  name: string;
  onSave: () => void;
  kind: "shortlist" | "reading list";
}) {
  const Icon = kind === "reading list" ? Bookmark : Heart;
  return (
    <button
      type="button"
      className={styles.save}
      data-saved={saved ? "true" : undefined}
      disabled={saved}
      aria-label={saved ? `${name} is on your ${kind}` : `Save ${name} to your ${kind}`}
      title={saved ? "Saved" : "Save"}
      onClick={onSave}
    >
      <motion.span
        key={saved ? "saved" : "save"}
        initial={{ scale: saved ? 0.4 : 1 }}
        animate={{ scale: 1 }}
        transition={{ type: "spring", stiffness: 500, damping: 18 }}
        className={styles.saveIcon}
      >
        <Icon aria-hidden="true" size={17} fill={saved ? "currentColor" : "none"} />
      </motion.span>
    </button>
  );
}

function Card({
  photo,
  title,
  meta,
  extra,
  onView,
  viewLabel,
  save,
  testId,
}: {
  photo: ReactNode;
  title: string;
  meta: ReactNode;
  extra?: ReactNode;
  onView: () => void;
  viewLabel: string;
  save: ReactNode;
  testId: string;
}) {
  return (
    <motion.li className={styles.card} variants={cardVariants} data-testid={testId}>
      <button type="button" className={styles.view} onClick={onView} aria-label={viewLabel}>
        <span className={styles.photo}>{photo}</span>
        <span className={styles.name}>{title}</span>
        <span className={styles.meta}>{meta}</span>
      </button>
      {extra ? <div className={styles.extra}>{extra}</div> : null}
      <div className={styles.saveSlot}>{save}</div>
    </motion.li>
  );
}

export function ProductCard({ product, reason }: { product: Product; reason?: string }) {
  const saved = useSession((s) => s.shortlist.some((p) => p.product_id === product.product_id));
  const { saveProduct } = useSessionActions();
  const details = useDetails();
  const name = productName(product);
  return (
    <Card
      testId="product-card"
      photo={
        <Photo sources={[productImageUrl(product.product_id)]} alt={name} ratio="2 / 3" paper />
      }
      title={name}
      meta={
        <>
          <strong className="tabular">{euro(product.price)}</strong> · {product.colour}
        </>
      }
      extra={reason ? <p className={styles.reason}>{reason}</p> : undefined}
      viewLabel={`View ${name}`}
      onView={() => {
        details.open({ kind: "product", id: product.product_id });
      }}
      save={
        <SaveButton
          kind="shortlist"
          name={name}
          saved={saved}
          onSave={() => {
            saveProduct(product);
          }}
        />
      }
    />
  );
}

export function AlcoholBadge({ cocktail }: { cocktail: Pick<Cocktail, "alcoholic"> }) {
  const [label, tone] = alcoholStatus(cocktail);
  return <Badge tone={tone}>{label}</Badge>;
}

export function CocktailCard({ cocktail, reason }: { cocktail: Cocktail; reason?: string }) {
  const saved = useSession((s) =>
    s.cocktailShortlist.some((c) => c.drink_id === cocktail.drink_id),
  );
  const { saveCocktail } = useSessionActions();
  const details = useDetails();
  return (
    <Card
      testId="cocktail-card"
      photo={<Photo sources={[drinkPhoto(cocktail)]} alt={cocktail.name} ratio="1" fit="cover" />}
      title={cocktail.name}
      meta={
        <>
          <AlcoholBadge cocktail={cocktail} />{" "}
          <span className={styles.glass}>{cocktail.glass}</span>
        </>
      }
      extra={reason ? <p className={styles.reason}>{reason}</p> : undefined}
      viewLabel={`View ${cocktail.name}`}
      onView={() => {
        details.open({ kind: "cocktail", id: cocktail.drink_id });
      }}
      save={
        <SaveButton
          kind="shortlist"
          name={cocktail.name}
          saved={saved}
          onSave={() => {
            saveCocktail(cocktail);
          }}
        />
      }
    />
  );
}

export function BookCover({ book, large = false }: { book: Book; large?: boolean }) {
  return (
    <Photo
      sources={coverSources(book, large).map((s) => s.url)}
      alt={`Cover of ${book.title}`}
      ratio="2 / 3"
      fit="cover"
      placeholder={book.title}
      className={styles.cover}
    />
  );
}

export function BookCard({ book, reason }: { book: Book; reason?: string }) {
  const saved = useSession((s) => s.readingList.some((b) => b.book_id === book.book_id));
  const { saveBook } = useSessionActions();
  const details = useDetails();
  return (
    <Card
      testId="book-card"
      photo={<BookCover book={book} large />}
      title={book.title}
      meta={
        <>
          {primaryAuthor(book)} ·{" "}
          <span className={styles.rating}>
            <Star aria-hidden="true" size={13} fill="currentColor" />
            <span className="visually-hidden">rated </span>
            {rating(book)}
          </span>
        </>
      }
      extra={reason ? <p className={styles.reason}>{reason}</p> : undefined}
      viewLabel={`View ${book.title}`}
      onView={() => {
        details.open({ kind: "book", id: book.book_id });
      }}
      save={
        <SaveButton
          kind="reading list"
          name={book.title}
          saved={saved}
          onSave={() => {
            saveBook(book);
          }}
        />
      }
    />
  );
}
