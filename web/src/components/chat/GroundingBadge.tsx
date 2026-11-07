import { ShieldAlert, ShieldCheck, Wand2 } from "lucide-react";
import { motion } from "motion/react";

import type { Domain, GroundingReport } from "../../api/types";
import { DOMAINS } from "../../domains/registry";
import { Badge } from "../../ui/Badge";

const ICONS = { verified: ShieldCheck, repaired: Wand2, fallback: ShieldAlert } as const;

/** Whether the answer's prices, titles and names were checked against what the tools returned. */
export function GroundingBadge({
  domain,
  grounding,
}: {
  domain: Domain;
  grounding: GroundingReport | null | undefined;
}) {
  const status = grounding?.status;
  if (!status || status === "not_applicable") return null;
  const badge = DOMAINS[domain].badges[status];
  const Icon = ICONS[status];
  const detail =
    grounding.checked > 0
      ? `${grounding.checked} ${grounding.checked === 1 ? "claim" : "claims"} checked`
      : undefined;
  return (
    <motion.span
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ type: "spring", stiffness: 420, damping: 26, delay: 0.1 }}
      style={{ display: "inline-flex" }}
      data-testid="grounding-badge"
      data-status={status}
    >
      <Badge tone={badge.tone} icon={<Icon />} title={detail}>
        {badge.label}
      </Badge>
    </motion.span>
  );
}
