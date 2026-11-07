import { CircleAlert, RefreshCw, SearchX } from "lucide-react";
import { motion } from "motion/react";
import type { ReactNode } from "react";

import { Button } from "./Button";
import styles from "./StateMessage.module.css";

/** An empty, error or hint state in the page's flow: an icon, a line and optional actions. */
export function StateMessage({
  tone = "empty",
  title,
  children,
  icon,
  action,
}: {
  tone?: "empty" | "error" | "hint";
  title?: ReactNode;
  children?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
}) {
  const glyph =
    icon ??
    (tone === "error" ? (
      <CircleAlert size={20} />
    ) : tone === "empty" ? (
      <SearchX size={20} />
    ) : null);
  return (
    <motion.div
      className={`${styles.state} ${styles[tone]}`}
      role={tone === "error" ? "alert" : "status"}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
    >
      {glyph ? (
        <span className={styles.icon} aria-hidden="true">
          {glyph}
        </span>
      ) : null}
      <div className={styles.text}>
        {title ? <p className={styles.title}>{title}</p> : null}
        {children ? <div className={styles.body}>{children}</div> : null}
        {action ? <div className={styles.action}>{action}</div> : null}
      </div>
    </motion.div>
  );
}

export function ErrorMessage({ error, retry }: { error: Error; retry?: () => void }) {
  return (
    <StateMessage
      tone="error"
      title={error.message}
      action={
        retry ? (
          <Button size="sm" icon={<RefreshCw />} onClick={retry}>
            Try again
          </Button>
        ) : undefined
      }
    />
  );
}
