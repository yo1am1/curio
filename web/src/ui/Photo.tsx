import { useState } from "react";

import styles from "./Photo.module.css";

/**
 * A photo with the Streamlit client's placeholder style: a dashed, tinted box with a short
 * caption when there is no photo or it fails to load. Several sources are tried in order.
 */
export function Photo({
  sources,
  alt,
  ratio = "3 / 4",
  fit = "contain",
  placeholder = "No photo available.",
  paper = false,
  className,
  sizes,
}: {
  sources: (string | null | undefined)[];
  alt: string;
  ratio?: string;
  fit?: "contain" | "cover";
  placeholder?: string;
  /** A white mat behind the photo (product shots on white). */
  paper?: boolean;
  className?: string;
  sizes?: string;
}) {
  const urls = sources.filter((s): s is string => !!s);
  const key = urls.join("|");
  const [state, setState] = useState({ key, index: 0, loaded: false });
  // A new set of sources (a swapped item) starts over.
  const current = state.key === key ? state : { key, index: 0, loaded: false };
  const url = urls[current.index];
  const frame = `${styles.frame} ${paper ? styles.paper : ""} ${className ?? ""}`;

  if (!url) {
    return (
      <div
        className={`${frame} ${styles.missing}`}
        style={{ aspectRatio: ratio }}
        role="img"
        aria-label={alt}
      >
        <span className={styles.missingText} aria-hidden="true">
          {placeholder}
        </span>
      </div>
    );
  }
  return (
    <div className={frame} style={{ aspectRatio: ratio }} data-loaded={current.loaded}>
      <img
        key={url}
        src={url}
        alt={alt}
        loading="lazy"
        decoding="async"
        sizes={sizes}
        className={styles.image}
        style={{ objectFit: fit }}
        onLoad={() => {
          setState({ key, index: current.index, loaded: true });
        }}
        onError={() => {
          setState({ key, index: current.index + 1, loaded: false });
        }}
      />
    </div>
  );
}
