import { useLayoutEffect, useRef, useState } from "react";

/** An element's content width, kept current with a ResizeObserver (`fallback` until known,
 * and in environments without one). */
export function useWidth<T extends HTMLElement>(fallback: number) {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(fallback);
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    // Measure before the first paint, so a phone never draws the fallback width.
    if (node.clientWidth > 0) setWidth(node.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const next = Math.round(entry?.contentRect.width ?? 0);
      if (next > 0) setWidth(next);
    });
    observer.observe(node);
    return () => {
      observer.disconnect();
    };
  }, []);
  return [ref, width] as const;
}
