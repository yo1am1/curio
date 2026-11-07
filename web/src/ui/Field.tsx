import { ChevronDown, Search } from "lucide-react";
import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";

import styles from "./Form.module.css";

/** A labelled form control: the label, the control (given its id) and an optional hint. */
export function Field({
  label,
  hint,
  children,
  className,
  labelHidden = false,
}: {
  label: ReactNode;
  hint?: ReactNode;
  children: (ids: { id: string; hintId?: string }) => ReactNode;
  className?: string;
  labelHidden?: boolean;
}) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className={`${styles.field} ${className ?? ""}`}>
      <label htmlFor={id} className={labelHidden ? "visually-hidden" : styles.label}>
        {label}
      </label>
      {children({ id, hintId })}
      {hint ? (
        <p id={hintId} className={styles.hint}>
          {hint}
        </p>
      ) : null}
    </div>
  );
}

/** A styled native select: the platform's own picker on phones, full keyboard support. */
export function Select({
  className,
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement> & { children: ReactNode }) {
  return (
    <span className={`${styles.selectWrap} ${className ?? ""}`}>
      <select className={styles.select} {...rest}>
        {children}
      </select>
      <ChevronDown className={styles.chevron} size={16} aria-hidden="true" />
    </span>
  );
}

/** A text input in the form style; `search` adds the magnifier. */
export function TextInput({
  search = false,
  className,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { search?: boolean }) {
  if (!search) return <input className={`${styles.input} ${className ?? ""}`} {...rest} />;
  return (
    <span className={styles.searchWrap}>
      <Search className={styles.searchIcon} size={18} aria-hidden="true" />
      <input className={`${styles.input} ${styles.searchInput} ${className ?? ""}`} {...rest} />
    </span>
  );
}
