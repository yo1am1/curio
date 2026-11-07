import { motion } from "motion/react";
import { useEffect, type ReactNode } from "react";

import styles from "./PageHeader.module.css";

export function PageHeader({
  title,
  intro,
  actions,
  documentTitle,
}: {
  title: string;
  intro?: string;
  actions?: ReactNode;
  documentTitle?: string;
}) {
  useEffect(() => {
    document.title = `${documentTitle ?? title} · Curio`;
  }, [documentTitle, title]);
  return (
    <motion.header
      className={styles.header}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
    >
      <div className={styles.text}>
        <h1>{title}</h1>
        {intro ? <p className={styles.intro}>{intro}</p> : null}
      </div>
      {actions ? <div className={styles.actions}>{actions}</div> : null}
    </motion.header>
  );
}
