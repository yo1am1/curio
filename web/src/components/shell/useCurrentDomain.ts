import { useLocation } from "react-router";

import type { Domain } from "../../api/types";
import { PAGE_ROOTS, isDomain } from "../../domains/registry";

/** The domain the current route belongs to: its pages (`/fashion/…`, `/drinks/…`,
 * `/reading/…`, `/evening`) and its chat (`/ask/<domain>`). */
export function domainOfPath(pathname: string): Domain | null {
  const [first, second] = pathname.split("/").filter(Boolean);
  if (first === "ask") return isDomain(second) ? second : null;
  return (first && PAGE_ROOTS[first]) || null;
}

export function useCurrentDomain(): Domain | null {
  return domainOfPath(useLocation().pathname);
}
