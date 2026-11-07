import { MessageCircle, RefreshCw } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { Link, NavLink, Outlet, useLocation } from "react-router";

import { useDomains, useHealth } from "../../api/queries";
import type { Domain } from "../../api/types";
import { DOMAINS, DOMAIN_PAGES, askPath } from "../../domains/registry";
import { Button } from "../../ui/Button";
import { SessionSheet } from "../session/SessionSheet";
import { DomainSwitch } from "./DomainSwitch";
import styles from "./AppShell.module.css";
import { SetupScreen } from "./SetupScreen";
import { useCurrentDomain } from "./useCurrentDomain";

function Brand() {
  return (
    <Link to="/" className={styles.brand} aria-label="Curio, home">
      <span className={styles.wordmark}>Curio</span>
      <span className={styles.tagline}>Trustworthy recommendation agents</span>
    </Link>
  );
}

/** The current domain's pages and its chat, with the session's saved list on the browse
 * pages. The row scrolls sideways on a phone instead of the page. */
function DomainPages({ domain }: { domain: Domain }) {
  const location = useLocation();
  const pages = [...DOMAIN_PAGES[domain], { to: askPath(domain), label: "Ask Curio" }];
  const onChat = location.pathname.startsWith("/ask/");
  return (
    <div className={styles.pagesRow}>
      <nav className={styles.pages} aria-label={`${DOMAINS[domain].label} pages`}>
        {pages.map((page) => (
          <NavLink
            key={page.to}
            to={page.to}
            end
            className={({ isActive }) => `${styles.page} ${isActive ? styles.pageActive : ""}`}
          >
            {page.to === askPath(domain) ? (
              <MessageCircle size={15} aria-hidden="true" className={styles.pageIcon} />
            ) : null}
            {page.label}
          </NavLink>
        ))}
      </nav>
      {onChat || domain === "evening" ? null : <SessionSheet domain={domain} />}
    </div>
  );
}

export function AppShell() {
  const domains = useDomains();
  const health = useHealth();
  const current = useCurrentDomain();
  const location = useLocation();
  const served = (domains.data ?? []).filter((d) => d.enabled);

  let main;
  if (domains.isPending) {
    main = <div className={styles.loading} aria-busy="true" aria-label="Loading Curio" />;
  } else if (domains.isError) {
    main = (
      <div className={styles.offline} role="alert">
        <h1>Curio cannot connect right now.</h1>
        <p>Please try again shortly.</p>
        <Button
          variant="primary"
          icon={<RefreshCw />}
          onClick={() => {
            void domains.refetch();
            void health.refetch();
          }}
        >
          Retry connection
        </Button>
      </div>
    );
  } else if (!served.length) {
    main = <SetupScreen domains={domains.data} />;
  } else {
    main = (
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={location.pathname}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
        >
          <Outlet />
        </motion.div>
      </AnimatePresence>
    );
  }

  return (
    <div className={styles.app}>
      <a className={styles.skip} href="#main">
        Skip to content
      </a>
      <header className={styles.header}>
        <div className={styles.bar}>
          <Brand />
          {domains.data && served.length ? (
            <DomainSwitch domains={domains.data} current={current} />
          ) : null}
        </div>
        {current && served.length ? <DomainPages domain={current} /> : null}
      </header>
      <main id="main" className={styles.main} tabIndex={-1}>
        {main}
      </main>
      <footer className={styles.footer}>
        <p>
          {current
            ? DOMAINS[current].credit(health.data)
            : "Curio · Trustworthy recommendation agents"}
        </p>
      </footer>
    </div>
  );
}
