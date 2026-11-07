import { createContext, useContext } from "react";

export type DetailTarget =
  | { kind: "product"; id: number }
  | { kind: "cocktail"; id: number }
  | { kind: "book"; id: number; pair?: boolean }
  /** The saved pieces or drinks side by side; an item opened from it stacks on top. */
  | { kind: "compare"; domain: "fashion" | "cocktails" };

export interface DetailsApi {
  /** Open an item's dialog; from inside one, the new item stacks (with a way back). */
  open: (target: DetailTarget) => void;
  close: () => void;
}

export const DetailsContext = createContext<DetailsApi>({
  open: () => undefined,
  close: () => undefined,
});

export function useDetails(): DetailsApi {
  return useContext(DetailsContext);
}
