import type { DomainInfo } from "../../api/types";
import { DOMAINS, DOMAIN_ORDER, QUICK_START } from "../../domains/registry";
import { DomainIcon } from "./DomainIcon";
import { PageHeader } from "./PageHeader";
import styles from "./SetupScreen.module.css";

/** Shown when the API serves no domain yet, instead of an empty app. */
export function SetupScreen({ domains }: { domains: DomainInfo[] }) {
  const byId = new Map(domains.map((d) => [d.id, d]));
  return (
    <div className={styles.setup}>
      <PageHeader
        title="Almost ready."
        documentTitle="Get started"
        intro="Curio's API is running, but no catalog is prepared on it yet."
      />
      <section className={styles.card} aria-labelledby="quick-start">
        <h2 id="quick-start" className={styles.title}>
          The quickest start
        </h2>
        <p>The cocktail catalog is about 260 KB. Prepare it, then restart the API:</p>
        <pre className={styles.code}>
          <code>{QUICK_START}</code>
        </pre>
        <p className={styles.note}>
          <code>docker compose --profile demo up</code> and <code>scripts/quickstart.sh</code> do
          this for you.
        </p>
      </section>
      <ul className={styles.domains}>
        {DOMAIN_ORDER.map((id) => (
          <li key={id} className={styles.domain}>
            <DomainIcon domain={id} size={18} />
            <div>
              <strong>{DOMAINS[id].label}</strong>
              <p>{byId.get(id)?.hint ?? DOMAINS[id].setupHint}</p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
