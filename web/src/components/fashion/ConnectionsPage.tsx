import { useMemo, useState } from "react";

import {
  type Connection,
  useConnections,
  useProductGraph,
  useRecommendations,
  useUserProfile,
} from "../../api/queries";
import type { Product, ProductHit } from "../../api/types";
import { euro, productName } from "../../domains/format";
import { usePageState } from "../../state/pageState";
import { Disclosure } from "../../ui/Disclosure";
import { Field, Select } from "../../ui/Field";
import { Segmented } from "../../ui/Segmented";
import { ErrorMessage, StateMessage } from "../../ui/StateMessage";
import { Shimmer } from "../../ui/Skeleton";
import { ItemGrid, ProductCard } from "../cards/ItemCards";
import { useDetails } from "../details/DetailsContext";
import browse from "../browse/Browse.module.css";
import { Results } from "../browse/Results";
import { PageHeader } from "../shell/PageHeader";
import { ConnectionsGraph, GraphLegend } from "./ConnectionsGraph";
import styles from "./Connections.module.css";
import { CustomerPicker } from "./CustomerPicker";
import { useCustomer } from "./useCustomer";
import { NO_CUSTOMER } from "./ForYouPage";
import { RELATION_LABELS, graphView, weightText, type GraphPoint } from "./graph";

const CONNECTIONS: { value: Connection; label: string; caption: string; empty: string }[] = [
  {
    value: "complement",
    label: "Bought together",
    caption: "Co-purchased products from different product groups.",
    empty: "No co-purchased products from different product groups.",
  },
  {
    value: "alternative",
    label: "Higher-price alternatives",
    caption:
      "Same specific Shopify category, product type and section, at a higher approximate price. Unclassified or broad categories are excluded. Price does not indicate quality or proven upsell value.",
    empty:
      "No higher-price alternatives with a matching specific Shopify category, product type and section. Unclassified or broad categories are excluded.",
  },
  {
    value: "similar",
    label: "Looks similar",
    caption:
      "Nearest product photos in the CLIP image index. No purchase data is used, so this works for products nobody has bought together yet.",
    empty: "No look-alikes are available for this piece.",
  },
];

interface Candidate {
  product: Product;
  purchased: boolean;
  reasons: string[];
}

/** The pieces a shopper can start from: their purchases, then their graph recommendations. */
function candidates(purchases: { product: Product }[], hits: ProductHit[]): Candidate[] {
  const seen = new Map<number, Candidate>();
  for (const { product } of purchases) {
    if (!seen.has(product.product_id)) {
      seen.set(product.product_id, { product, purchased: true, reasons: [] });
    }
  }
  for (const hit of hits) {
    const id = hit.product.product_id;
    if (!seen.has(id)) {
      seen.set(id, { product: hit.product, purchased: false, reasons: hit.reasons ?? [] });
    }
  }
  return [...seen.values()];
}

function Relationships({
  view,
  active,
}: {
  view: ReturnType<typeof graphView>;
  active: string | null;
}) {
  return (
    <Disclosure summary={`Relationships · ${String(view.links.length)}`}>
      <p className={browse.caption} style={{ marginBottom: "0.6rem" }}>
        This view shows up to 12 nodes and 24 relationships. Node spacing does not measure
        similarity.
      </p>
      {/* Scrolls sideways on a phone, so the keyboard must be able to reach it. */}
      <div className={styles.tableWrap} role="region" aria-label="Relationships" tabIndex={0}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col">From</th>
              <th scope="col">Relationship</th>
              <th scope="col">To</th>
              <th scope="col">Strength</th>
            </tr>
          </thead>
          <tbody>
            {view.links.map((link) => (
              <tr
                key={`${link.source}-${link.target}-${link.relation}`}
                data-active={active === link.target || active === link.source ? "true" : undefined}
              >
                <td>{link.sourceLabel}</td>
                <td>{RELATION_LABELS[link.relation]}</td>
                <td>{link.targetLabel}</td>
                <td className={styles.num}>{weightText(link)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Disclosure>
  );
}

function Explore({ focus, focusName }: { focus: number; focusName: string }) {
  const graph = useProductGraph(focus);
  const details = useDetails();
  const [selected, setSelected] = useState<GraphPoint | null>(null);
  const [kind, setKind] = usePageState<Connection>(
    "fashion.connection",
    "complement",
    (v): v is Connection => v === "complement" || v === "alternative" || v === "similar",
  );
  const view = useMemo(
    () => (graph.data ? graphView(graph.data, focus) : null),
    [graph.data, focus],
  );
  const relatedId = selected?.productId ?? focus;
  const relatedName = selected?.productId ? selected.label : focusName;
  const related = useConnections(relatedId, kind);
  const info = CONNECTIONS.find((c) => c.value === kind) ?? CONNECTIONS[0];

  return (
    <>
      <section className={styles.explore} aria-labelledby="explore-title">
        <div className={styles.exploreHead}>
          <h2 id="explore-title" className={browse.resultsTitle}>
            Explore the connections
          </h2>
          <p className={browse.caption}>
            Hover or focus a node to see how it connects. Select a piece to open it; its related
            products appear below.
          </p>
        </div>
        {graph.isPending ? (
          <Shimmer height="26rem" />
        ) : graph.error ? (
          <ErrorMessage
            error={graph.error}
            retry={() => {
              void graph.refetch();
            }}
          />
        ) : view && view.links.length ? (
          <>
            <ConnectionsGraph
              view={view}
              selected={selected?.id ?? null}
              onSelect={(point) => {
                setSelected(point.focus ? null : point);
              }}
              onOpen={(productId) => {
                details.open({ kind: "product", id: productId });
              }}
            />
            <GraphLegend />
            <Relationships view={view} active={selected?.id ?? null} />
          </>
        ) : (
          <StateMessage>No relationships are available for this product.</StateMessage>
        )}
      </section>
      <section className={browse.page} aria-labelledby="related-title">
        <div className={styles.exploreHead}>
          <h2 id="related-title" className={browse.resultsTitle}>
            Related products · {relatedName}
          </h2>
          <Segmented
            label="Relationship"
            options={CONNECTIONS.map((c) => ({ value: c.value, label: c.label }))}
            value={kind}
            wrap
            onChange={setKind}
          />
        </div>
        <Results
          title={info?.label ?? "Related"}
          titleHidden
          caption={info?.caption}
          query={related}
          enabled
          kind="product"
          noun="piece"
          empty={info?.empty}
        >
          {(hits) => (
            <ItemGrid label={`Related to ${relatedName}`}>
              {hits.map((hit) => (
                <ProductCard key={hit.product.product_id} product={hit.product} detailed />
              ))}
            </ItemGrid>
          )}
        </Results>
      </section>
    </>
  );
}

/** Product connections: a shopper's piece, its neighborhood in the purchase and catalog graph,
 * and the pieces related to it. */
export function ConnectionsPage() {
  const { customer, examples, setCustomer, error, isPending } = useCustomer();
  const userId = customer?.user_id ?? null;
  const profile = useUserProfile(userId);
  const graphPicks = useRecommendations(userId, "graph", 10);
  const [stored, setStored] = usePageState<{ customer: number; product: number } | null>(
    "fashion.focus",
    null,
    (v): v is { customer: number; product: number } =>
      v === null || (typeof v === "object" && "product" in v),
  );
  const options = useMemo(
    () => candidates(profile.data?.purchases ?? [], graphPicks.data ?? []),
    [profile.data, graphPicks.data],
  );
  const ids = options.map((o) => o.product.product_id);
  const focus =
    stored && stored.customer === userId && ids.includes(stored.product)
      ? stored.product
      : customer?.focus_product_id && ids.includes(customer.focus_product_id)
        ? customer.focus_product_id
        : (ids[0] ?? null);
  const current = options.find((o) => o.product.product_id === focus);
  const loading = isPending || (userId !== null && (profile.isPending || graphPicks.isPending));

  return (
    <div className={browse.page}>
      <PageHeader
        title="Product connections"
        documentTitle="Product connections · Fashion"
        intro="Start from a piece a shopper bought or was recommended, and explore what connects it to the rest of the catalog."
      />
      <section className={browse.panel} aria-label="Choose a starting piece">
        <div className={styles.focusPanel}>
          {error ? (
            <ErrorMessage error={error} />
          ) : isPending ? (
            <Shimmer height="2.85rem" />
          ) : (
            <CustomerPicker customer={customer} examples={examples} onChange={setCustomer} />
          )}
          <div className={styles.focusPick}>
            {loading ? (
              <Shimmer height="2.85rem" />
            ) : options.length ? (
              <>
                <Field label="Starting piece">
                  {({ id }) => (
                    <Select
                      id={id}
                      value={focus ?? ""}
                      onChange={(event) => {
                        if (userId !== null) {
                          setStored({ customer: userId, product: Number(event.target.value) });
                        }
                      }}
                    >
                      {options.map(({ product, purchased }) => (
                        <option key={product.product_id} value={product.product_id}>
                          {purchased ? "Purchased" : "Recommended"} · {productName(product)} (#
                          {product.product_id})
                        </option>
                      ))}
                    </Select>
                  )}
                </Field>
                {current ? (
                  <p className={styles.focusMeta}>
                    Shopping history #{userId} · {current.product.product_type} ·{" "}
                    <span className="tabular">{euro(current.product.price)}</span>
                  </p>
                ) : null}
                {current?.reasons.length ? (
                  <ul className={styles.reasons}>
                    {current.reasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                ) : null}
              </>
            ) : userId !== null ? (
              <StateMessage>
                No purchases or graph recommendations are available for this customer.
              </StateMessage>
            ) : null}
          </div>
        </div>
      </section>
      {userId === null && !isPending && !error ? (
        <StateMessage tone="hint">{NO_CUSTOMER}</StateMessage>
      ) : null}
      {focus !== null && current ? (
        <Explore key={focus} focus={focus} focusName={productName(current.product)} />
      ) : null}
    </div>
  );
}
