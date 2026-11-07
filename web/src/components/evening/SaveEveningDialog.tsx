import { Check, X } from "lucide-react";
import { useMemo, useState } from "react";

import type { EveningPlan } from "../../api/types";
import { useSessionActions } from "../../state/actions";
import { Button } from "../../ui/Button";
import { Dialog } from "../../ui/Dialog";
import { useToast } from "../../ui/toast";
import { ApprovalOptions } from "../chat/ApprovalOptions";
import { eveningOptions } from "./plan";

const LISTS: Record<string, string> = {
  product_ids: "shortlist",
  book_ids: "reading list",
  drink_ids: "saved cocktails",
};

/** The planner page's approval step: every part is listed and can be unticked before saving,
 * then each joins its own list, as the agent's SaveEvening effect does. */
export function SaveEveningDialog({
  plan,
  open,
  onClose,
}: {
  plan: EveningPlan;
  open: boolean;
  onClose: () => void;
}) {
  const options = useMemo(() => eveningOptions(plan), [plan]);
  const [unticked, setUnticked] = useState<ReadonlySet<string>>(new Set());
  const { applyReply } = useSessionActions();
  const toast = useToast();
  const kept = options.filter((option) => !unticked.has(option.key));
  const ids = (field: string) =>
    kept.filter((o) => o.field === field).map((o) => o.value as number);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="sm"
      title="Save this evening?"
      description="Curio saves each part to its own list. Untick anything you don't want."
      footer={
        <>
          <Button
            variant="primary"
            icon={<Check />}
            disabled={!kept.length}
            data-autofocus
            onClick={() => {
              const products = (plan.outfit?.outfit.slots ?? []).flatMap((s) =>
                s.product ? [s.product] : [],
              );
              void applyReply({
                applied: [
                  {
                    kind: "evening",
                    product_ids: ids("product_ids"),
                    book_ids: ids("book_ids"),
                    drink_ids: ids("drink_ids"),
                  },
                ],
                products,
                books: plan.book ? [plan.book.book] : [],
                cocktails: plan.drink ? [plan.drink.cocktail] : [],
              });
              setUnticked(new Set());
              onClose();
            }}
          >
            Approve
          </Button>
          <Button
            icon={<X />}
            onClick={() => {
              toast("Nothing was saved.", "info");
              setUnticked(new Set());
              onClose();
            }}
          >
            Decline
          </Button>
        </>
      }
    >
      <ApprovalOptions
        options={options}
        unticked={unticked}
        suffix={(option) => LISTS[option.field]}
        onToggle={(key, keep) => {
          const next = new Set(unticked);
          if (keep) next.delete(key);
          else next.add(key);
          setUnticked(next);
        }}
      />
    </Dialog>
  );
}
