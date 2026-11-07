import { useLocation } from "react-router";

import type { Domain } from "../../api/types";
import { isDomain } from "../../domains/registry";

/** The domain the current route belongs to: /evening and /ask/<domain>. */
export function domainOfPath(pathname: string): Domain | null {
  const [first, second] = pathname.split("/").filter(Boolean);
  if (first === "evening") return "evening";
  if (first === "ask" && isDomain(second)) return second;
  return null;
}

export function useCurrentDomain(): Domain | null {
  return domainOfPath(useLocation().pathname);
}
