import type { UseQueryResult } from "@tanstack/react-query";
import { motion } from "motion/react";
import { Fragment, useId, type ReactNode } from "react";

import { plural } from "../../domains/format";
import { GridSkeleton } from "../../ui/Skeleton";
import { ErrorMessage, StateMessage } from "../../ui/StateMessage";
import styles from "./Browse.module.css";

/**
 * A results section for a query: a skeleton while the first answer loads, the error with a
 * retry, an empty state, or the items (dimmed while newer ones load). Nothing to search yet
 * shows `idle`.
 */
export function Results<T>({
  title,
  query,
  enabled,
  kind,
  idle,
  empty,
  caption,
  noun = "result",
  titleHidden = false,
  children,
}: {
  title: ReactNode;
  query: Pick<
    UseQueryResult<T[]>,
    | "data"
    | "error"
    | "isPending"
    | "isFetching"
    | "isPlaceholderData"
    | "refetch"
    | "dataUpdatedAt"
  >;
  enabled: boolean;
  kind: "product" | "cocktail" | "book";
  idle?: ReactNode;
  empty: ReactNode;
  caption?: ReactNode;
  noun?: string;
  /** The heading is for screen readers only (a visible one sits above). */
  titleHidden?: boolean;
  children: (items: T[]) => ReactNode;
}) {
  const headingId = useId();
  if (!enabled) {
    return idle ? <StateMessage tone="hint">{idle}</StateMessage> : null;
  }
  const items = query.data;
  const stale = query.isFetching && query.isPlaceholderData;
  return (
    <motion.section
      className={styles.results}
      aria-labelledby={headingId}
      aria-busy={query.isFetching}
      data-stale={stale ? "true" : undefined}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      <div className={styles.resultsHead}>
        <h2 id={headingId} className={titleHidden ? "visually-hidden" : styles.resultsTitle}>
          {title}
        </h2>
        {items?.length ? <span className={styles.count}>{plural(items.length, noun)}</span> : null}
      </div>
      {caption ? <p className={styles.caption}>{caption}</p> : null}
      {query.error && !items ? (
        <ErrorMessage
          error={query.error}
          retry={() => {
            void query.refetch();
          }}
        />
      ) : query.isPending || !items ? (
        <GridSkeleton kind={kind} count={kind === "cocktail" ? 6 : 8} />
      ) : items.length === 0 ? (
        <StateMessage>{empty}</StateMessage>
      ) : (
        // New results replay the cards' staggered entry.
        <Fragment key={query.dataUpdatedAt}>{children(items)}</Fragment>
      )}
    </motion.section>
  );
}
