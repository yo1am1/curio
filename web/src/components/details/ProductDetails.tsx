import { Heart, MessageCircle } from "lucide-react";
import { useEffect, useState } from "react";

import { productImageUrl } from "../../api/client";
import { useProduct, useProductRelations } from "../../api/queries";
import { FASHION_NOTE } from "../../domains/registry";
import { euro, productName } from "../../domains/format";
import { useSession } from "../../state/stores";
import { useSessionActions } from "../../state/actions";
import { Button } from "../../ui/Button";
import { Photo } from "../../ui/Photo";
import { Segmented } from "../../ui/Segmented";
import { useDetails } from "./DetailsContext";
import styles from "./Details.module.css";
import { DetailError, DetailSkeleton } from "./states";
import { useAskCurio } from "./useAskCurio";

const RELATIONS = [
  { value: "similar", label: "Similar styles" },
  { value: "complement", label: "Bought together" },
] as const;

export function ProductDetails({ id, onTitle }: { id: number; onTitle: (title: string) => void }) {
  const { data: product, error, isPending } = useProduct(id);
  const saved = useSession((s) => s.shortlist.some((p) => p.product_id === id));
  const { saveProduct } = useSessionActions();
  const ask = useAskCurio("fashion");
  const [relation, setRelation] = useState<"similar" | "complement">("similar");
  const name = product ? productName(product) : "";
  useEffect(() => {
    if (name) onTitle(name);
  }, [name, onTitle]);

  if (isPending) return <DetailSkeleton ratio="2 / 3" />;
  if (error) return <DetailError error={error} />;

  return (
    <>
      <div className={styles.layout}>
        <div className={styles.media}>
          <Photo
            sources={[productImageUrl(id)]}
            alt={name}
            ratio="2 / 3"
            paper
            placeholder="A photo is not available for this item."
          />
        </div>
        <div className={styles.info}>
          <p className={styles.kicker}>{product.section}</p>
          <h2 className={styles.name}>{name}</h2>
          <p className={styles.price}>{euro(product.price)}</p>
          <p className={styles.facts}>
            {product.product_type} · {product.colour}
          </p>
          <p className={styles.text}>
            {product.description || "No further description is available."}
          </p>
          <div className={styles.actions}>
            <Button
              variant={saved ? "secondary" : "primary"}
              icon={<Heart fill={saved ? "currentColor" : "none"} />}
              disabled={saved}
              onClick={() => {
                saveProduct(product);
              }}
              data-autofocus
            >
              {saved ? "Saved" : "Save to shortlist"}
            </Button>
            {ask ? (
              <Button
                icon={<MessageCircle />}
                onClick={() => {
                  ask(`What goes with the ${name} (product ${id})?`);
                }}
              >
                Ask Curio what goes with it
              </Button>
            ) : null}
          </div>
          <p className={styles.note}>{FASHION_NOTE}</p>
        </div>
      </div>
      <section className={styles.section} aria-label="Explore this piece">
        <Segmented
          label="Explore this piece"
          options={RELATIONS}
          value={relation}
          onChange={setRelation}
          size="sm"
        />
        <RelatedProducts id={id} relation={relation} />
      </section>
    </>
  );
}

function RelatedProducts({ id, relation }: { id: number; relation: "similar" | "complement" }) {
  const { data, isPending } = useProductRelations(id, relation);
  const details = useDetails();
  if (isPending) {
    return (
      <ul className={styles.tiles} aria-busy="true">
        {[0, 1, 2, 3].map((n) => (
          <li key={n} className={styles.skeleton} style={{ aspectRatio: "2 / 3" }} />
        ))}
      </ul>
    );
  }
  if (!data?.length) return <p className={styles.empty}>No related pieces are available yet.</p>;
  return (
    <ul className={styles.tiles}>
      {data.map(({ product }) => (
        <li key={product.product_id}>
          <button
            type="button"
            className={styles.tile}
            onClick={() => {
              details.open({ kind: "product", id: product.product_id });
            }}
          >
            <Photo
              sources={[productImageUrl(product.product_id)]}
              alt={productName(product)}
              ratio="2 / 3"
              paper
            />
            <span className={styles.relatedName}>{productName(product)}</span>
            <span className={styles.relatedMeta}>
              {product.colour} · {euro(product.price)}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
