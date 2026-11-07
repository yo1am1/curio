import type { ReactNode } from "react";

import type { Tone } from "../domains/registry";
import styles from "./Badge.module.css";

export function Badge({
  tone = "gray",
  icon,
  children,
  title,
  className,
}: {
  tone?: Tone;
  icon?: ReactNode;
  children: ReactNode;
  title?: string;
  className?: string;
}) {
  return (
    <span className={`${styles.badge} ${styles[tone]} ${className ?? ""}`} title={title}>
      {icon ? (
        <span className={styles.icon} aria-hidden="true">
          {icon}
        </span>
      ) : null}
      <span>{children}</span>
    </span>
  );
}
