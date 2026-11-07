import { motion } from "motion/react";
import { useId } from "react";
import { Link } from "react-router";

import type { Domain, DomainInfo } from "../../api/types";
import { DOMAINS, landingPath } from "../../domains/registry";
import { DomainIcon } from "./DomainIcon";
import styles from "./DomainSwitch.module.css";

/** Fashion · Cocktails · Books · Evening, from `/domains`. A domain the server does not serve
 * stays visible but disabled, with the server's hint on how to prepare it. */
export function DomainSwitch({
  domains,
  current,
}: {
  domains: DomainInfo[];
  current: Domain | null;
}) {
  const id = useId();
  return (
    <nav className={styles.switch} aria-label="Catalog">
      <ul className={styles.list}>
        {domains.map((info) => {
          const view = DOMAINS[info.id];
          const active = info.id === current;
          if (!info.enabled) {
            const hintId = `${id}-${info.id}`;
            return (
              <li key={info.id} className={styles.item}>
                <span
                  className={`${styles.option} ${styles.disabled}`}
                  tabIndex={0}
                  aria-disabled="true"
                  aria-describedby={hintId}
                >
                  <DomainIcon domain={info.id} size={17} />
                  <span className={styles.label}>{info.label || view.label}</span>
                </span>
                <span id={hintId} role="tooltip" className={styles.tooltip}>
                  <strong>{view.unavailable}.</strong> {info.hint ?? view.setupHint}
                </span>
              </li>
            );
          }
          return (
            <li key={info.id} className={styles.item}>
              <Link
                to={landingPath(info.id)}
                className={styles.option}
                aria-current={active ? "page" : undefined}
                data-active={active ? "true" : undefined}
              >
                {active ? (
                  <motion.span
                    layoutId="domain-indicator"
                    className={styles.indicator}
                    transition={{ type: "spring", stiffness: 480, damping: 38 }}
                  />
                ) : null}
                <DomainIcon domain={info.id} size={17} />
                <span className={styles.label}>{info.label || view.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
