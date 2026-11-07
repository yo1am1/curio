import { ArrowLeft } from "lucide-react";
import { useCallback, useMemo, useState, type ReactNode } from "react";

import { Button } from "../../ui/Button";
import { Dialog } from "../../ui/Dialog";
import { BookDetails } from "./BookDetails";
import { CocktailDetails } from "./CocktailDetails";
import { DetailsContext, type DetailTarget } from "./DetailsContext";
import { ProductDetails } from "./ProductDetails";

const TITLES = { product: "Product details", cocktail: "Recipe", book: "Book" } as const;

/** One dialog for every item: products, recipes and books. Related items opened from inside
 * stack, so "Back" returns to the item before. */
export function DetailsProvider({ children }: { children: ReactNode }) {
  const [stack, setStack] = useState<DetailTarget[]>([]);
  const [title, setTitle] = useState<string | null>(null);
  const open = useCallback((target: DetailTarget) => {
    setTitle(null);
    setStack((current) => [...current, target].slice(-8));
  }, []);
  const close = useCallback(() => {
    setStack([]);
  }, []);
  const back = useCallback(() => {
    setTitle(null);
    setStack((current) => current.slice(0, -1));
  }, []);
  const api = useMemo(() => ({ open, close }), [open, close]);
  const target = stack[stack.length - 1];
  const key = target ? `${target.kind}-${target.id}-${stack.length}` : "none";

  return (
    <DetailsContext.Provider value={api}>
      {children}
      <Dialog
        open={!!target}
        onClose={close}
        size="lg"
        title={title ?? (target ? TITLES[target.kind] : "")}
        titleHidden
      >
        {stack.length > 1 ? (
          <Button variant="ghost" size="sm" icon={<ArrowLeft />} onClick={back}>
            Back
          </Button>
        ) : null}
        {target?.kind === "product" ? (
          <ProductDetails key={key} id={target.id} onTitle={setTitle} />
        ) : null}
        {target?.kind === "cocktail" ? (
          <CocktailDetails key={key} id={target.id} onTitle={setTitle} />
        ) : null}
        {target?.kind === "book" ? (
          <BookDetails key={key} id={target.id} pair={target.pair} onTitle={setTitle} />
        ) : null}
      </Dialog>
    </DetailsContext.Provider>
  );
}
