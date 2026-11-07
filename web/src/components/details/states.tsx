import styles from "./Details.module.css";

export function DetailSkeleton({ ratio }: { ratio: string }) {
  return (
    <div className={styles.layout} aria-busy="true" aria-label="Loading">
      <div className={styles.skeleton} style={{ aspectRatio: ratio }} />
      <div className={styles.info}>
        <div className={styles.skeleton} style={{ width: "30%" }} />
        <div className={styles.skeleton} style={{ height: "2.2rem", width: "75%" }} />
        <div className={styles.skeleton} style={{ width: "45%" }} />
        <div className={styles.skeleton} style={{ height: "6rem" }} />
      </div>
    </div>
  );
}

export function DetailError({ error }: { error: Error }) {
  return (
    <p className={`${styles.empty} ${styles.error}`} role="alert">
      {error.message}
    </p>
  );
}
