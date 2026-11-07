import { useId, type ReactNode } from "react";

import styles from "./Toggle.module.css";

/** A switch: a native checkbox with role="switch", so it keeps keyboard and form behaviour. */
export function Toggle({
  checked,
  onChange,
  children,
  hint,
  disabled,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  children: ReactNode;
  hint?: string;
  disabled?: boolean;
}) {
  const hintId = useId();
  return (
    <label className={styles.toggle} data-disabled={disabled ? "true" : undefined}>
      <input
        type="checkbox"
        role="switch"
        className={styles.input}
        checked={checked}
        disabled={disabled}
        aria-describedby={hint ? hintId : undefined}
        onChange={(event) => {
          onChange(event.target.checked);
        }}
      />
      <span className={styles.track} aria-hidden="true">
        <span className={styles.thumb} />
      </span>
      <span className={styles.text}>
        {children}
        {hint ? (
          <span id={hintId} className={styles.hint}>
            {hint}
          </span>
        ) : null}
      </span>
    </label>
  );
}
