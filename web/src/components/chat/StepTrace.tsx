import {
  Ban,
  Check,
  ChevronDown,
  CircleAlert,
  Hourglass,
  Lightbulb,
  ListTree,
  MinusCircle,
  PenLine,
  ShieldCheck,
  UserCheck,
  Wrench,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useId, useState } from "react";

import type { AgentStep } from "../../api/types";
import { stepLabel } from "../../domains/registry";
import styles from "./StepTrace.module.css";
import { traceLabel } from "./trace";

const KIND_ICONS = {
  plan: Lightbulb,
  tool: Wrench,
  guardrail: ShieldCheck,
  approval: UserCheck,
  answer: PenLine,
} as const;

const STATUS = {
  ok: { icon: Check, label: "done" },
  error: { icon: CircleAlert, label: "failed" },
  blocked: { icon: Ban, label: "blocked" },
  pending: { icon: Hourglass, label: "waiting for you" },
  skipped: { icon: MinusCircle, label: "skipped" },
} as const;

function timing(step: AgentStep): string | null {
  const s = step.duration_ms / 1000;
  return s >= 0.1 ? `${s.toFixed(1)}s` : null;
}

function StepRow({ step, index, live }: { step: AgentStep; index: number; live: boolean }) {
  const Kind = KIND_ICONS[step.kind];
  const status = STATUS[step.status];
  const StatusIcon = status.icon;
  const time = timing(step);
  return (
    <motion.li
      className={styles.step}
      data-kind={step.kind}
      data-status={step.status}
      initial={live ? { opacity: 0, x: -10 } : false}
      animate={{ opacity: 1, x: 0 }}
      transition={{ type: "spring", stiffness: 420, damping: 34, delay: live ? 0 : index * 0.03 }}
    >
      <span className={styles.node} aria-hidden="true">
        <Kind size={14} />
      </span>
      <div className={styles.content}>
        <div className={styles.head}>
          <span className={styles.name}>{stepLabel(step.name)}</span>
          <span className={styles.status} title={status.label}>
            <StatusIcon size={13} aria-hidden="true" />
            <span className="visually-hidden">{status.label}</span>
          </span>
          {time ? <span className={styles.time}>{time}</span> : null}
        </div>
        <p className={styles.summary}>{step.summary}</p>
      </div>
    </motion.li>
  );
}

/** The agent's steps as a timeline: live while a turn streams, then folded under "How Curio
 * worked", which opens on demand. */
export function StepTrace({
  steps,
  live = false,
  defaultOpen = false,
}: {
  steps: AgentStep[];
  live?: boolean;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const listId = useId();
  if (live) {
    const current = steps[steps.length - 1];
    return (
      <div className={`${styles.trace} ${styles.live}`} aria-live="polite" aria-busy="true">
        <div className={styles.liveHead}>
          <span className={styles.pulse} aria-hidden="true" />
          <span>{current ? `${stepLabel(current.name)}…` : "Curio is planning…"}</span>
        </div>
        <ol className={styles.list} aria-label="Steps so far">
          <AnimatePresence initial={false}>
            {steps.map((step, index) => (
              <StepRow key={`${index}-${step.name}`} step={step} index={index} live />
            ))}
          </AnimatePresence>
        </ol>
      </div>
    );
  }
  if (!steps.length) return null;
  return (
    <div className={styles.trace}>
      <button
        type="button"
        className={styles.toggle}
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => {
          setOpen(!open);
        }}
      >
        <ListTree size={16} aria-hidden="true" />
        <span>{traceLabel(steps)}</span>
        <motion.span
          className={styles.chevron}
          animate={{ rotate: open ? 180 : 0 }}
          transition={{ duration: 0.2 }}
          aria-hidden="true"
        >
          <ChevronDown size={16} />
        </motion.span>
      </button>
      <AnimatePresence initial={false}>
        {open ? (
          <motion.div
            id={listId}
            key="steps"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
            style={{ overflow: "hidden" }}
          >
            <ol className={styles.list}>
              {steps.map((step, index) => (
                <StepRow key={`${index}-${step.name}`} step={step} index={index} live={false} />
              ))}
            </ol>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
