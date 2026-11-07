import { Bookmark, Heart, Layers } from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";
import { useNavigate } from "react-router";

import type { Domain } from "../../api/types";
import { askPath } from "../../domains/registry";
import { useSession } from "../../state/stores";
import { Dialog } from "../../ui/Dialog";
import { SessionPanel } from "./SessionPanel";
import styles from "./SessionSheet.module.css";

/** "Your session" on the browse pages: the saved list's count in the sub-navigation, opening
 * the session panel (approvals, memory, the list and Compare) in a dialog. */
export function SessionSheet({ domain }: { domain: Domain }) {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const count = useSession((s) =>
    domain === "fashion"
      ? s.shortlist.length
      : domain === "cocktails"
        ? s.cocktailShortlist.length
        : domain === "books"
          ? s.readingList.length
          : s.shortlist.length + s.readingList.length + s.cocktailShortlist.length,
  );
  const Icon = domain === "books" ? Bookmark : domain === "evening" ? Layers : Heart;
  const noun = domain === "books" ? "Reading list" : domain === "evening" ? "Saved" : "Shortlist";
  return (
    <>
      <button
        type="button"
        className={styles.trigger}
        aria-haspopup="dialog"
        aria-label={`Your session: ${noun.toLocaleLowerCase()}, ${String(count)} saved`}
        onClick={() => {
          setOpen(true);
        }}
      >
        <Icon size={16} aria-hidden="true" fill={count ? "currentColor" : "none"} />
        <span className={styles.noun}>{noun}</span>
        <motion.span
          key={count}
          className={styles.count}
          initial={{ scale: 0.6, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ type: "spring", stiffness: 500, damping: 22 }}
        >
          {count}
        </motion.span>
      </button>
      <Dialog
        open={open}
        onClose={() => {
          setOpen(false);
        }}
        title="Your session"
        size="sm"
      >
        <SessionPanel
          domain={domain}
          embedded
          onLeave={() => {
            setOpen(false);
          }}
          onAsk={(message) => {
            void navigate(askPath(domain), { state: { ask: message } });
          }}
        />
      </Dialog>
    </>
  );
}
