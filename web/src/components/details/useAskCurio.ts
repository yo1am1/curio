import { useNavigate } from "react-router";

import { useServed } from "../../api/queries";
import type { Domain } from "../../api/types";
import { askPath } from "../../domains/registry";
import { useDetails } from "./DetailsContext";

/** "Ask Curio about it": close the dialog and send `message` in `domain`'s chat. Null when
 * that chat is not available on this server. */
export function useAskCurio(domain: Domain): ((message: string) => void) | null {
  const navigate = useNavigate();
  const { close } = useDetails();
  const served = useServed().get(domain);
  if (!served?.enabled || !served.chat_enabled) return null;
  return (message: string) => {
    close();
    void navigate(askPath(domain), { state: { ask: message } });
  };
}
