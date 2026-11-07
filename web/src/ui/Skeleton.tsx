import styles from "./Skeleton.module.css";

/** Placeholder cards while results load, shaped like the cards they stand in for. */
export function GridSkeleton({
  kind = "product",
  count = 6,
  label = "Loading results",
}: {
  kind?: "product" | "cocktail" | "book";
  count?: number;
  label?: string;
}) {
  const ratio = kind === "cocktail" ? "1" : "2 / 3";
  return (
    <div
      className={`${styles.grid} ${styles[kind]}`}
      role="status"
      aria-busy="true"
      aria-label={label}
    >
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className={styles.card} style={{ animationDelay: `${index * 70}ms` }}>
          <div className={styles.block} style={{ aspectRatio: ratio }} />
          <div className={styles.line} style={{ width: "80%" }} />
          <div className={styles.line} style={{ width: "45%" }} />
        </div>
      ))}
    </div>
  );
}

export function Shimmer({ height = "1rem", width = "100%" }: { height?: string; width?: string }) {
  return <div className={styles.line} style={{ height, width }} aria-hidden="true" />;
}
