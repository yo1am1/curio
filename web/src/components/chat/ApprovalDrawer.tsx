import { Check, ShieldQuestion, X } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { PendingAction } from "../../api/types";
import { approvalOptions, editedArgs, keepsSomething } from "../../state/approval";
import { Button } from "../../ui/Button";
import { ApprovalOptions } from "./ApprovalOptions";
import styles from "./ApprovalDrawer.module.css";

/**
 * The agent proposed a change (save, remember): nothing happens until the shopper approves.
 * They can untick items or remembered values to narrow the proposal; `onDecide` gets the
 * `edited_args` the API expects (only the changed fields, or null).
 */
export function ApprovalDrawer({
  action,
  onDecide,
  busy = false,
  error,
}: {
  action: PendingAction;
  onDecide: (approved: boolean, editedArgs: Record<string, unknown> | null) => void;
  busy?: boolean;
  error?: string;
}) {
  const options = useMemo(() => approvalOptions(action), [action]);
  const [unticked, setUnticked] = useState<ReadonlySet<string>>(new Set());
  const region = useRef<HTMLElement>(null);
  useEffect(() => {
    region.current?.focus({ preventScroll: true });
  }, [action.id]);
  const keeps = keepsSomething(options, unticked);
  return (
    <motion.section
      ref={region}
      tabIndex={-1}
      className={styles.drawer}
      aria-labelledby={`approval-${action.id}`}
      data-testid="approval-drawer"
      initial={{ opacity: 0, y: 28 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 20, transition: { duration: 0.18 } }}
      transition={{ type: "spring", stiffness: 340, damping: 32 }}
    >
      <div className={styles.grip} aria-hidden="true" />
      <header className={styles.header}>
        <span className={styles.icon} aria-hidden="true">
          <ShieldQuestion size={18} />
        </span>
        <div>
          <h2 id={`approval-${action.id}`} className={styles.title}>
            Needs your approval
          </h2>
          {action.summary ? <p className={styles.summary}>{action.summary}</p> : null}
        </div>
      </header>
      {error ? (
        <p className={styles.error} role="alert">
          {error}
        </p>
      ) : null}
      <ApprovalOptions
        options={options}
        unticked={unticked}
        disabled={busy}
        onToggle={(key, kept) => {
          const next = new Set(unticked);
          if (kept) next.delete(key);
          else next.add(key);
          setUnticked(next);
        }}
      />
      <footer className={styles.footer}>
        <Button
          variant="primary"
          icon={<Check />}
          disabled={busy || !keeps}
          onClick={() => {
            onDecide(true, editedArgs(action, unticked));
          }}
        >
          Approve
        </Button>
        <Button
          icon={<X />}
          disabled={busy}
          onClick={() => {
            onDecide(false, null);
          }}
        >
          Decline
        </Button>
        <p className={styles.hint}>
          {busy
            ? "Sending your decision…"
            : options.length
              ? "Untick anything you don't want before approving."
              : "Nothing changes until you approve."}
        </p>
      </footer>
    </motion.section>
  );
}
