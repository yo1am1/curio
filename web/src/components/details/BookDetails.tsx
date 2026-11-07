import { Bookmark, ExternalLink, MessageCircle, RefreshCw, Star, Wine } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { postJson } from "../../api/client";
import { useBook, useBooksByAuthor, useServed, useSimilarBooks } from "../../api/queries";
import type { BookHit, BookPairing, PairingRequest } from "../../api/types";
import { BOOK_CREDIT } from "../../domains/registry";
import {
  OPEN_LIBRARY_BOOK,
  bookFacts,
  coverSources,
  primaryAuthor,
  rating,
  ratingLine,
} from "../../domains/format";
import { useSession } from "../../state/stores";
import { useSessionActions } from "../../state/actions";
import { Badge } from "../../ui/Badge";
import { Button } from "../../ui/Button";
import { Toggle } from "../../ui/Toggle";
import { BookCover } from "../cards/ItemCards";
import { PairingCard } from "../cards/PairingCard";
import { useDetails } from "./DetailsContext";
import styles from "./Details.module.css";
import { DetailError, DetailSkeleton } from "./states";
import { useAskCurio } from "./useAskCurio";

export function BookDetails({
  id,
  pair = false,
  onTitle,
}: {
  id: number;
  pair?: boolean;
  onTitle: (title: string) => void;
}) {
  const { data: book, error, isPending } = useBook(id);
  const saved = useSession((s) => s.readingList.some((b) => b.book_id === id));
  const { saveBook } = useSessionActions();
  const ask = useAskCurio("books");
  const served = useServed();
  const pairable = !!served.get("books")?.enabled && !!served.get("cocktails")?.enabled;
  const [pairing, setPairing] = useState(pair);
  const [more, setMore] = useState(false);
  const similar = useSimilarBooks(id);
  const byAuthor = useBooksByAuthor(id);
  useEffect(() => {
    if (book) onTitle(book.title);
  }, [book, onTitle]);

  if (isPending) return <DetailSkeleton ratio="2 / 3" />;
  if (error) return <DetailError error={error} />;

  const facts = bookFacts(book);
  const openLibrary = coverSources(book, true)[0]?.openLibrary;
  const long = book.description.length > 420;
  return (
    <>
      <div className={`${styles.layout} ${styles.narrow}`}>
        <div className={styles.media}>
          <BookCover book={book} large />
          {openLibrary && book.isbn ? (
            <p className={styles.note} style={{ marginTop: "0.5rem" }}>
              Cover:{" "}
              <a href={OPEN_LIBRARY_BOOK.replace("{}", book.isbn)} target="_blank" rel="noreferrer">
                Open Library
              </a>
            </p>
          ) : null}
        </div>
        <div className={styles.info}>
          <h2 className={styles.name}>{book.title}</h2>
          <p className={styles.byline}>by {primaryAuthor(book)}</p>
          <div className={styles.chips}>
            <Badge tone="violet" icon={<Star fill="currentColor" />}>
              {ratingLine(book)}
            </Badge>
          </div>
          {facts ? <p className={styles.facts}>{facts}</p> : null}
          {book.genres?.length ? (
            <div className={styles.chips} aria-label="Genres">
              {book.genres.map((genre) => (
                <Badge key={genre} tone="blue">
                  {genre}
                </Badge>
              ))}
            </div>
          ) : null}
          {book.description ? (
            <div>
              <p className={`${styles.text} ${long && !more ? styles.clamped : ""}`}>
                {book.description}
              </p>
              {long ? (
                <Button
                  variant="link"
                  size="sm"
                  aria-expanded={more}
                  onClick={() => {
                    setMore(!more);
                  }}
                >
                  {more ? "Show less" : "Read more"}
                </Button>
              ) : null}
            </div>
          ) : null}
          {book.tags?.length ? (
            <p className={styles.note}>Readers shelve it as {book.tags.slice(0, 8).join(", ")}</p>
          ) : null}
          <div className={styles.actions}>
            <Button
              variant={saved ? "secondary" : "primary"}
              icon={<Bookmark fill={saved ? "currentColor" : "none"} />}
              disabled={saved}
              onClick={() => {
                saveBook(book);
              }}
              data-autofocus
            >
              {saved ? "On your reading list" : "Save to reading list"}
            </Button>
            {pairable ? (
              <Button
                icon={<Wine />}
                aria-expanded={pairing}
                aria-controls={`pairing-${id}`}
                pressed={pairing}
                onClick={() => {
                  setPairing(!pairing);
                }}
              >
                Pair a drink
              </Button>
            ) : null}
            {ask ? (
              <Button
                icon={<MessageCircle />}
                onClick={() => {
                  ask(`Tell me about ${book.title} (book ${id}).`);
                }}
              >
                Ask Curio about it
              </Button>
            ) : null}
            <a
              className={styles.note}
              href={book.source_url}
              target="_blank"
              rel="noreferrer"
              style={{ display: "inline-flex", alignItems: "center", gap: "0.3rem" }}
            >
              Goodreads <ExternalLink size={14} aria-hidden="true" />
            </a>
          </div>
        </div>
      </div>
      <AnimatePresence initial={false}>
        {pairable && pairing ? (
          <motion.section
            id={`pairing-${id}`}
            className={styles.section}
            aria-label="Pour me something to read with"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
            style={{ overflow: "hidden" }}
          >
            <PairingPanel bookId={id} />
          </motion.section>
        ) : null}
      </AnimatePresence>
      <div className={`${styles.section} ${styles.columns}`}>
        <RelatedBooks
          title="Readers also liked"
          hits={similar}
          empty="No shared readers for this book yet."
        />
        <RelatedBooks
          title={`More by ${primaryAuthor(book)}`}
          hits={byAuthor}
          empty="No other books by this author in the catalog."
          hideBy
        />
      </div>
      <p className={styles.note} style={{ marginTop: "1.25rem" }}>
        {BOOK_CREDIT}
      </p>
    </>
  );
}

function PairingPanel({ bookId }: { bookId: number }) {
  const [alcoholFree, setAlcoholFree] = useState(false);
  const [seed, setSeed] = useState(0);
  const avoided = useSession((s) => s.bookPreferences.avoided_genres);
  const tastes = useSession((s) => s.cocktailPreferences);
  const body: PairingRequest = {
    book_id: bookId,
    alcohol_free: alcoholFree,
    seed,
    ...(avoided.length ? { avoided_genres: avoided } : {}),
    ...(tastes.alcohol_free || tastes.liked_ingredients.length || tastes.avoided_ingredients.length
      ? {
          cocktail_preferences: Object.fromEntries(
            Object.entries(tastes).filter(([, v]) => (Array.isArray(v) ? v.length : v)),
          ),
        }
      : {}),
  } as PairingRequest;
  const { data, error, isPending, isFetching } = useQuery({
    queryKey: ["pairing", body],
    queryFn: ({ signal }) => postJson<BookPairing>("/pairings/book-cocktail", body, signal),
    staleTime: 5 * 60_000,
    placeholderData: (previous) => previous,
  });
  const sets = data?.sets ?? 1;
  return (
    <div style={{ display: "grid", gap: "0.85rem" }} aria-busy={isFetching}>
      <div
        style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem 1.5rem", alignItems: "center" }}
      >
        <h3 className={styles.sectionTitle} style={{ marginRight: "auto" }}>
          <Wine size={16} aria-hidden="true" style={{ verticalAlign: "-2px", marginRight: 6 }} />
          Pour me something to read with
        </h3>
        <Toggle
          checked={alcoholFree}
          onChange={(value) => {
            setAlcoholFree(value);
            setSeed(0);
          }}
        >
          Alcohol-free only
        </Toggle>
        <Button
          variant="secondary"
          size="sm"
          icon={<RefreshCw />}
          disabled={sets < 2 || isFetching}
          title="Another set of drinks for the same profile"
          onClick={() => {
            setSeed((seed + 1) % Math.max(sets, 1));
          }}
        >
          Another pairing
        </Button>
      </div>
      {isPending ? <div className={styles.skeleton} style={{ height: "12rem" }} /> : null}
      {error ? <DetailError error={error} /> : null}
      {data ? <PairingCard result={data} /> : null}
    </div>
  );
}

function RelatedBooks({
  title,
  hits,
  empty,
  hideBy = false,
}: {
  title: string;
  hits: { data?: BookHit[]; isPending: boolean };
  empty: string;
  hideBy?: boolean;
}) {
  const details = useDetails();
  return (
    <section>
      <h3 className={styles.sectionTitle} style={{ marginBottom: "0.5rem" }}>
        {title}
      </h3>
      {hits.isPending ? <div className={styles.skeleton} style={{ height: "10rem" }} /> : null}
      {!hits.isPending && !hits.data?.length ? <p className={styles.empty}>{empty}</p> : null}
      {hits.data?.length ? (
        <ul className={styles.related}>
          {hits.data.map(({ book, reasons }) => {
            // "By <author>" repeats the column heading; show the series instead.
            const reason = (reasons ?? []).find((r) => !hideBy || !r.startsWith("By "));
            return (
              <li key={book.book_id}>
                <button
                  type="button"
                  className={styles.relatedItem}
                  onClick={() => {
                    details.open({ kind: "book", id: book.book_id });
                  }}
                >
                  <span className={styles.relatedThumb}>
                    <BookCover book={book} />
                  </span>
                  <span>
                    <span className={styles.relatedName}>{book.title}</span>
                    <br />
                    <span className={styles.relatedMeta}>
                      ★ {rating(book)}
                      {reason ? ` · ${reason}` : ""}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}
