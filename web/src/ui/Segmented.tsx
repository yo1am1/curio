import { motion } from "motion/react";
import { useId, type ReactNode } from "react";

import styles from "./Segmented.module.css";

export interface SegmentOption<T extends string> {
  value: T;
  label: ReactNode;
  icon?: ReactNode;
  disabled?: boolean;
  title?: string;
}

/** A single-choice segmented control (a radio group with a sliding indicator). */
export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
  size = "md",
  allowNone = false,
}: {
  label: string;
  options: readonly SegmentOption<T>[];
  value: T | null;
  onChange: (value: T) => void;
  size?: "sm" | "md";
  /** Clicking the chosen option again clears it (onChange gets the same value). */
  allowNone?: boolean;
}) {
  const group = useId();
  return (
    <div className={`${styles.group} ${styles[size]}`} role="radiogroup" aria-label={label}>
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={option.disabled}
            title={option.title}
            className={styles.option}
            data-active={active ? "true" : undefined}
            tabIndex={active || (value === null && option === options[0]) ? 0 : -1}
            onClick={() => {
              if (!active || allowNone) onChange(option.value);
            }}
            onKeyDown={(event) => {
              const enabled = options.filter((o) => !o.disabled);
              const at = enabled.findIndex((o) => o.value === option.value);
              const step =
                event.key === "ArrowRight" || event.key === "ArrowDown"
                  ? 1
                  : event.key === "ArrowLeft" || event.key === "ArrowUp"
                    ? -1
                    : 0;
              if (!step || at === -1) return;
              event.preventDefault();
              const next = enabled[(at + step + enabled.length) % enabled.length];
              if (!next) return;
              onChange(next.value);
              const parent = event.currentTarget.parentElement;
              const target = parent?.querySelector<HTMLButtonElement>(
                `[data-value="${next.value}"]`,
              );
              target?.focus();
            }}
            data-value={option.value}
          >
            {active ? (
              <motion.span
                layoutId={`segment-${group}`}
                className={styles.indicator}
                transition={{ type: "spring", stiffness: 500, damping: 38 }}
              />
            ) : null}
            {option.icon ? (
              <span className={styles.icon} aria-hidden="true">
                {option.icon}
              </span>
            ) : null}
            <span className={styles.label}>{option.label}</span>
          </button>
        );
      })}
    </div>
  );
}
