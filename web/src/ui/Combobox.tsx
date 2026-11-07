import { Check, ChevronDown, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useId, useMemo, useRef, useState, type KeyboardEvent } from "react";

import styles from "./Combobox.module.css";
import { filterOptions } from "./filterOptions";

export interface ComboboxProps<T extends string | number> {
  label: string;
  options: readonly T[];
  value: readonly T[];
  onChange: (value: T[]) => void;
  /** Option text; defaults to the value itself. */
  optionLabel?: (option: T) => string;
  /** Several values as removable chips (default), or one value shown in the input. */
  multiple?: boolean;
  max?: number;
  placeholder?: string;
  hint?: string;
  /** Options rendered at once; typing narrows the rest. */
  limit?: number;
  disabled?: boolean;
  labelHidden?: boolean;
}

/**
 * An accessible combobox (the WAI-ARIA 1.2 list-autocomplete pattern): type to filter, arrow
 * keys to move, Enter to pick, Escape to close, Backspace on an empty input removes the last
 * chip. Long lists (10,000 book titles) render only the first `limit` matches.
 */
export function Combobox<T extends string | number>({
  label,
  options,
  value,
  onChange,
  optionLabel = (option) => String(option),
  multiple = true,
  max,
  placeholder,
  hint,
  limit = 60,
  disabled = false,
  labelHidden = false,
}: ComboboxProps<T>) {
  const id = useId();
  const inputId = `${id}-input`;
  const listId = `${id}-list`;
  const hintId = `${id}-hint`;
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);

  const selected = useMemo(() => new Set<T>(value), [value]);
  const matches = useMemo(
    () => filterOptions(options, query, optionLabel),
    [options, query, optionLabel],
  );
  const shown = matches.slice(0, limit);
  const full = multiple && max !== undefined && value.length >= max;
  const single = !multiple ? value[0] : undefined;

  function moveTo(index: number) {
    const next = Math.max(0, Math.min(index, shown.length - 1));
    setActive(next);
    list.current
      ?.querySelector<HTMLElement>(`[data-index="${next}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }

  function pick(option: T) {
    if (!multiple) {
      onChange(selected.has(option) ? [] : [option]);
      setQuery("");
      setOpen(false);
      return;
    }
    if (selected.has(option)) {
      onChange(value.filter((v) => v !== option));
    } else if (!full) {
      onChange([...value, option]);
      setQuery("");
      setActive(0);
    }
  }

  function remove(option: T) {
    onChange(value.filter((v) => v !== option));
    input.current?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (!open) {
          setOpen(true);
          setActive(0);
        } else moveTo(active + 1);
        break;
      case "ArrowUp":
        event.preventDefault();
        if (open) moveTo(active - 1);
        break;
      case "Home":
      case "End":
        if (open && shown.length) {
          event.preventDefault();
          moveTo(event.key === "Home" ? 0 : shown.length - 1);
        }
        break;
      case "Enter": {
        const option = shown[active];
        if (open && option !== undefined) {
          event.preventDefault();
          pick(option);
        }
        break;
      }
      case "Escape":
        if (open) {
          event.preventDefault();
          event.stopPropagation();
          setOpen(false);
        } else if (query) {
          event.preventDefault();
          setQuery("");
        }
        break;
      case "Backspace":
        if (multiple && !query && value.length) {
          onChange(value.slice(0, -1));
        }
        break;
      case "Tab":
        setOpen(false);
        break;
      default:
        break;
    }
  }

  const activeOption = open ? shown[active] : undefined;
  const inputText = !multiple && !open && single !== undefined ? optionLabel(single) : query;

  return (
    <div
      ref={root}
      className={styles.root}
      data-open={open ? "true" : undefined}
      onBlur={(event) => {
        if (!root.current?.contains(event.relatedTarget)) {
          setOpen(false);
          setQuery("");
        }
      }}
    >
      <label
        htmlFor={inputId}
        className={labelHidden ? "visually-hidden" : styles.label}
        id={`${id}-label`}
      >
        {label}
      </label>
      <div className={styles.anchor}>
        <div
          className={styles.control}
          data-disabled={disabled ? "true" : undefined}
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              event.preventDefault();
              input.current?.focus();
              setOpen(true);
            }
          }}
        >
          {multiple && value.length ? (
            <ul className={styles.chips} aria-label={`Chosen ${label.toLocaleLowerCase()}`}>
              <AnimatePresence initial={false}>
                {value.map((option) => (
                  <motion.li
                    key={String(option)}
                    className={styles.chip}
                    layout
                    initial={{ opacity: 0, scale: 0.85 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.85 }}
                    transition={{ duration: 0.15 }}
                  >
                    <span className={styles.chipText}>{optionLabel(option)}</span>
                    <button
                      type="button"
                      className={styles.chipRemove}
                      aria-label={`Remove ${optionLabel(option)}`}
                      disabled={disabled}
                      onClick={() => {
                        remove(option);
                      }}
                    >
                      <X size={13} aria-hidden="true" />
                    </button>
                  </motion.li>
                ))}
              </AnimatePresence>
            </ul>
          ) : null}
          <input
            ref={input}
            id={inputId}
            className={styles.input}
            role="combobox"
            aria-expanded={open}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={
              activeOption !== undefined ? `${id}-option-${String(active)}` : undefined
            }
            aria-describedby={hint || full ? hintId : undefined}
            autoComplete="off"
            spellCheck={false}
            disabled={disabled}
            placeholder={multiple && value.length ? "" : placeholder}
            value={inputText}
            onChange={(event) => {
              setQuery(event.target.value);
              setOpen(true);
              setActive(0);
            }}
            onFocus={() => {
              if (!multiple) setQuery("");
            }}
            onClick={() => {
              setOpen(true);
            }}
            onKeyDown={onKeyDown}
          />
          {!multiple && single !== undefined && !open ? (
            <button
              type="button"
              className={styles.clear}
              aria-label={`Clear ${label.toLocaleLowerCase()}`}
              onClick={() => {
                onChange([]);
                input.current?.focus();
              }}
            >
              <X size={15} aria-hidden="true" />
            </button>
          ) : (
            <ChevronDown className={styles.chevron} size={16} aria-hidden="true" />
          )}
        </div>
        <AnimatePresence>
          {open ? (
            <motion.div
              className={styles.popup}
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4, transition: { duration: 0.1 } }}
              transition={{ duration: 0.14 }}
            >
              <ul
                ref={list}
                id={listId}
                role="listbox"
                aria-labelledby={`${id}-label`}
                aria-multiselectable={multiple || undefined}
                className={styles.list}
              >
                {shown.map((option, index) => {
                  const isSelected = selected.has(option);
                  const isDisabled = full && !isSelected;
                  return (
                    <li
                      key={String(option)}
                      id={`${id}-option-${String(index)}`}
                      role="option"
                      aria-selected={isSelected}
                      aria-disabled={isDisabled || undefined}
                      data-index={index}
                      data-active={index === active ? "true" : undefined}
                      className={styles.option}
                      onMouseDown={(event) => {
                        event.preventDefault();
                      }}
                      onMouseMove={() => {
                        if (index !== active) setActive(index);
                      }}
                      onClick={() => {
                        if (!isDisabled) pick(option);
                      }}
                    >
                      <span className={styles.check} aria-hidden="true">
                        {isSelected ? <Check size={15} /> : null}
                      </span>
                      <span className={styles.optionText}>{optionLabel(option)}</span>
                    </li>
                  );
                })}
                {shown.length === 0 ? (
                  <li className={styles.none} role="presentation">
                    No matches for “{query.trim()}”.
                  </li>
                ) : null}
              </ul>
              {matches.length > shown.length ? (
                <p className={styles.more}>
                  Showing {shown.length} of {matches.length.toLocaleString("en-US")}. Keep typing to
                  narrow it down.
                </p>
              ) : null}
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>
      {hint || full ? (
        <p id={hintId} className={styles.hint}>
          {full ? `You can choose up to ${String(max)}.` : hint}
        </p>
      ) : null}
      <span className="visually-hidden" aria-live="polite">
        {open && query ? `${String(matches.length)} matches` : ""}
      </span>
    </div>
  );
}
