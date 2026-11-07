import { ShieldAlert, ShieldCheck, Wand2 } from "lucide-react";
import { motion } from "motion/react";

import type { Domain, GroundingReport } from "../../api/types";
import { DOMAINS } from "../../domains/registry";
import { Badge } from "../../ui/Badge";
import { describeChecked } from "./grounding";

const ICONS = { verified: ShieldCheck, repaired: Wand2, fallback: ShieldAlert } as const;

/**
 * What the grounding check did with the answer: which facts it checked against the tools'
 * results ("Checked 3 prices and 2 names against the catalog"), that it had the answer
 * rewritten, or that it replaced the answer with catalog facts. Nothing when the answer
 * stated nothing checkable.
 */
export function GroundingBadge({
  domain,
  grounding,
}: {
  domain: Domain;
  grounding: GroundingReport | null | undefined;
}) {
  const status = grounding?.status;
  if (!status || status === "not_applicable") return null;
  if (status === "verified" && grounding.checked < 1) return null;
  const badges = DOMAINS[domain].badges;
  const Icon = ICONS[status];
  const checked = grounding.checked > 0 ? describeChecked(grounding) : "";
  const label =
    status === "verified"
      ? `Checked ${checked} against ${badges.verified.against}`
      : badges[status].label;
  const issues = grounding.issues?.length ?? 0;
  const title =
    status === "verified"
      ? undefined
      : `${issues} ${issues === 1 ? "claim" : "claims"} did not match the tools' results` +
        (checked ? `; checked ${checked}` : "");
  return (
    <motion.span
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ type: "spring", stiffness: 420, damping: 26, delay: 0.1 }}
      style={{ display: "inline-flex" }}
      data-testid="grounding-badge"
      data-status={status}
    >
      <Badge tone={badges[status].tone} icon={<Icon />} title={title}>
        {label}
      </Badge>
    </motion.span>
  );
}
