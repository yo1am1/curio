/** A page's inputs (queries, filters, the chosen shopper), kept for the browser tab like the
 * Streamlit client's remembered inputs: they survive a reload and a visit to another page. */
import { useCallback, useState } from "react";

import { browserStorage, readStorage, writeStorage } from "./store";

const PREFIX = "curio.page.";

export function usePageState<T>(
  key: string,
  initial: T,
  valid: (value: unknown) => value is T = (value): value is T =>
    value !== null && typeof value === typeof initial,
): [T, (next: T | ((current: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
    const stored = readStorage(browserStorage("sessionStorage"), PREFIX + key);
    return valid(stored) ? stored : initial;
  });
  const update = useCallback(
    (next: T | ((current: T) => T)) => {
      setValue((current) => {
        const result = typeof next === "function" ? (next as (current: T) => T)(current) : next;
        writeStorage(browserStorage("sessionStorage"), PREFIX + key, result);
        return result;
      });
    },
    [key],
  );
  return [value, update];
}

export const isStringList = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((v) => typeof v === "string");
export const isNumberList = (value: unknown): value is number[] =>
  Array.isArray(value) && value.every((v) => typeof v === "number");
export const isNumberOrNull = (value: unknown): value is number | null =>
  value === null || typeof value === "number";
export const isStringOrNull = (value: unknown): value is string | null =>
  value === null || typeof value === "string";
export const oneOf =
  <T extends string | number>(options: readonly T[]) =>
  (value: unknown): value is T =>
    (options as readonly unknown[]).includes(value);
