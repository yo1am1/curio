import { ChevronRight } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useId, useState, type ReactNode } from "react";

import styles from "./Disclosure.module.css";

/** A button that shows and hides a region below it (aria-expanded), with a height animation. */
export function Disclosure({
  summary,
  children,
  defaultOpen = false,
  className,
  size = "md",
}: {
  summary: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
  size?: "sm" | "md";
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <div className={`${styles.disclosure} ${styles[size]} ${className ?? ""}`}>
      <button
        type="button"
        className={styles.summary}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => {
          setOpen((value) => !value);
        }}
      >
        <ChevronRight
          className={styles.chevron}
          size={size === "sm" ? 14 : 16}
          aria-hidden="true"
          data-open={open ? "true" : undefined}
        />
        <span>{summary}</span>
      </button>
      <AnimatePresence initial={false}>
        {open ? (
          <motion.div
            id={id}
            key="content"
            className={styles.content}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
          >
            <div className={styles.inner}>{children}</div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
