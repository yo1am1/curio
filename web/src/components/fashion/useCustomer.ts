import { useDemoCustomers } from "../../api/queries";
import type { DemoCustomer } from "../../api/types";
import { usePageState } from "../../state/pageState";

export const CUSTOMER_KEY = "fashion.customer";

/** A shopping history: an explained example, or any customer by id. */
export type Shopper = Pick<DemoCustomer, "user_id"> & Partial<DemoCustomer>;

/** The example shopper the fashion pages use, shared by For you and Product connections:
 * the chosen one, else the first example. */
export function useCustomer(): {
  customer: Shopper | null;
  examples: DemoCustomer[];
  setCustomer: (id: number | null) => void;
  isPending: boolean;
  error: Error | null;
} {
  const demo = useDemoCustomers(true);
  const [chosen, setChosen] = usePageState<number | null>(
    CUSTOMER_KEY,
    null,
    (v): v is number | null => v === null || typeof v === "number",
  );
  const examples = (demo.data ?? []).slice(0, 10);
  const id = chosen ?? examples[0]?.user_id ?? null;
  const customer: Shopper | null =
    id === null ? null : (examples.find((e) => e.user_id === id) ?? { user_id: id });
  return {
    customer,
    examples,
    setCustomer: setChosen,
    isPending: demo.isPending,
    error: demo.error,
  };
}
