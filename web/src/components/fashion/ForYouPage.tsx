import { ArrowRight, History, Info } from "lucide-react";
import { Link } from "react-router";

import { productImageUrl } from "../../api/client";
import { useHealth, useRecommendations, useUserProfile, type Strategy } from "../../api/queries";
import { QUALITY_PATH } from "../../domains/registry";
import { euro, productName } from "../../domains/format";
import { oneOf, usePageState } from "../../state/pageState";
import { Disclosure } from "../../ui/Disclosure";
import { Photo } from "../../ui/Photo";
import { Segmented } from "../../ui/Segmented";
import { ErrorMessage, StateMessage } from "../../ui/StateMessage";
import { Shimmer } from "../../ui/Skeleton";
import { ItemGrid, ProductCard } from "../cards/ItemCards";
import { useDetails } from "../details/DetailsContext";
import browse from "../browse/Browse.module.css";
import { Results } from "../browse/Results";
import { PageHeader } from "../shell/PageHeader";
import { CustomerPicker } from "./CustomerPicker";
import { useCustomer } from "./useCustomer";
import styles from "./Fashion.module.css";
import { STRATEGIES, availableStrategies, recommendationReason } from "./strategies";

export const NO_CUSTOMER = "No shopping history is selected. Choose one to see picks for it.";

function RecentPurchases({ userId }: { userId: number }) {
  const profile = useUserProfile(userId);
  const details = useDetails();
  const purchases = profile.data?.purchases.slice(0, 10) ?? [];
  return (
    <Disclosure
      summary={
        <span className={styles.summaryLabel}>
          <History size={15} aria-hidden="true" /> Recent purchases (up to 10)
        </span>
      }
    >
      {profile.isPending ? (
        <Shimmer height="4rem" />
      ) : profile.error ? (
        <ErrorMessage error={profile.error} />
      ) : purchases.length ? (
        <ul className={styles.purchases} aria-label="Recent purchases">
          {purchases.map(({ product, purchase_date }, index) => (
            <li key={`${String(product.product_id)}-${String(index)}`}>
              <button
                type="button"
                className={styles.purchase}
                onClick={() => {
                  details.open({ kind: "product", id: product.product_id });
                }}
              >
                <span className={styles.purchaseThumb}>
                  <Photo
                    sources={[productImageUrl(product.product_id)]}
                    alt=""
                    ratio="2 / 3"
                    paper
                    placeholder=""
                  />
                </span>
                <span className={styles.purchaseText}>
                  <span className={styles.purchaseName}>{productName(product)}</span>
                  <span className={styles.purchaseMeta}>
                    {product.product_type} · <span className="tabular">{euro(product.price)}</span>
                  </span>
                </span>
                <time className={styles.purchaseDate} dateTime={purchase_date}>
                  {purchase_date}
                </time>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className={styles.caption}>No purchases are recorded for this history.</p>
      )}
    </Disclosure>
  );
}

function HowPicksAreChosen() {
  return (
    <Disclosure
      summary={
        <span className={styles.summaryLabel}>
          <Info size={15} aria-hidden="true" /> How picks are chosen
        </span>
      }
    >
      <div className={styles.explain}>
        <p>
          Buy again is a strong simple baseline. Personalized ranking learns from earlier purchases;
          discovery approaches deliberately show only new items.
        </p>
        <dl className={styles.strategyList}>
          {STRATEGIES.map((strategy) => (
            <div key={strategy.id}>
              <dt>{strategy.label}</dt>
              <dd>{strategy.description}</dd>
            </div>
          ))}
        </dl>
        <Link to={QUALITY_PATH} className={styles.qualityLink}>
          How each approach scores on what people bought next
          <ArrowRight size={15} aria-hidden="true" />
        </Link>
      </div>
    </Disclosure>
  );
}

/** For you: a shopping history's picks, by the strategy chosen, with their evidence. */
export function ForYouPage() {
  const health = useHealth();
  const strategies = availableStrategies(!!health.data?.ranking_enabled);
  const ids = strategies.map((s) => s.id);
  const [chosen, setStrategy] = usePageState<Strategy>(
    "fashion.strategy",
    "learned",
    oneOf<Strategy>(["learned", "repeat", "collaborative", "content", "graph"]),
  );
  const strategy = ids.includes(chosen) ? chosen : (ids[0] ?? "repeat");
  const info = STRATEGIES.find((s) => s.id === strategy);
  const { customer, examples, setCustomer, error, isPending } = useCustomer();
  const userId = customer?.user_id ?? null;
  const picks = useRecommendations(userId, strategy);

  return (
    <div className={browse.page}>
      <PageHeader
        title="For you"
        documentTitle="For you · Fashion"
        intro="Picks from a real shopping history in the H&M research data. Choose a history and how Curio picks for it."
      />
      <section className={`${browse.panel} ${styles.feedPanel}`} aria-label="Shopping history">
        <div className={styles.feedControls}>
          {error ? (
            <ErrorMessage error={error} />
          ) : isPending ? (
            <Shimmer height="2.85rem" />
          ) : (
            <CustomerPicker customer={customer} examples={examples} onChange={setCustomer} />
          )}
          <div className={browse.control}>
            <span className={browse.controlLabel} aria-hidden="true">
              Strategy
            </span>
            <Segmented
              label="Strategy"
              options={strategies.map((s) => ({ value: s.id, label: s.label }))}
              value={strategy}
              wrap
              onChange={setStrategy}
            />
            {info ? <p className={styles.caption}>{info.description}</p> : null}
          </div>
        </div>
        <div className={styles.disclosures}>
          {userId !== null ? <RecentPurchases userId={userId} /> : null}
          <HowPicksAreChosen />
        </div>
      </section>
      {userId === null && !isPending && !error ? (
        <StateMessage tone="hint">{NO_CUSTOMER}</StateMessage>
      ) : null}
      <Results
        title={info?.label ?? "Picks"}
        query={picks}
        enabled={userId !== null}
        kind="product"
        noun="pick"
        empty="No picks are available for this history with this approach."
      >
        {(hits) => (
          <ItemGrid label="Picks for this shopping history">
            {hits.map((hit, index) => (
              <ProductCard
                key={`${String(hit.product.product_id)}-${String(index)}`}
                product={hit.product}
                detailed
                reason={recommendationReason(hit.reasons ?? [])}
                evidence={hit.reasons ?? []}
                score={hit.score}
              />
            ))}
          </ItemGrid>
        )}
      </Results>
    </div>
  );
}
