import { ShieldAlert, ShieldCheck, Wand2 } from "lucide-react";
import { motion } from "motion/react";

import type { Domain, GroundingReport } from "../../api/types";
import { DOMAINS } from "../../domains/registry";
import { Badge } from "../../ui/Badge";
import { describeChecked, describeCounts, facts, renderedTotal } from "./grounding";

const ICONS = { verified: ShieldCheck, repaired: Wand2, fallback: ShieldAlert } as const;

/**
 * What grounding did with the answer. In cite mode the server renders catalog facts from the
 * model's fact references, and a backstop checks the values the model typed itself: "5 facts
 * from the catalog · 1 more checked against it", with the kinds in the tooltip. A report without rendered
 * facts (an older server, or nothing cited) says what was checked ("Checked 3 prices and 2
 * names against the catalog"). It also says when the answer was rewritten, or replaced with
 * catalog facts. Nothing when the answer stated nothing.
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
  if (status === "verified" && grounding.checked < 1 && renderedTotal(grounding) < 1) return null;
  const badges = DOMAINS[domain].badges;
  const against = badges.verified.against;
  const Icon = ICONS[status];
  const failed = status === "verified" ? "" : badges[status].label;
  const { label, title } = describe(grounding, status, against, failed);
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

/** The badge's label and tooltip for a report that is not `not_applicable`. */
function describe(
  grounding: GroundingReport,
  status: "verified" | "repaired" | "fallback",
  against: string,
  failedLabel: string,
): { label: string; title?: string } {
  const rendered = renderedTotal(grounding);
  const checked = grounding.checked > 0 ? describeChecked(grounding) : "";
  const fromCatalog = rendered > 0 ? describeCounts(grounding.rendered, rendered) : "";
  if (status === "verified" && rendered > 0) {
    return {
      label:
        `${facts(rendered)} from ${against}` +
        (grounding.checked > 0
          ? ` · ${grounding.checked} more checked against ${against.endsWith("s") ? "them" : "it"}`
          : ""),
      title:
        `Rendered by the server from ${against}: ${fromCatalog}` +
        (checked ? `. Typed by the model and checked: ${checked}` : ""),
    };
  }
  if (status === "verified") return { label: `Checked ${checked} against ${against}` };
  const issues = grounding.issues?.length ?? 0;
  return {
    label: failedLabel,
    title:
      `${issues} ${issues === 1 ? "claim" : "claims"} did not match the tools' results` +
      (checked ? `; checked ${checked}` : "") +
      (fromCatalog ? `; shown from ${against}: ${fromCatalog}` : ""),
  };
}
