import type { Variants } from "motion/react";

/** Cards enter one after another. */
export const gridVariants: Variants = {
  hidden: {},
  shown: { transition: { staggerChildren: 0.06, delayChildren: 0.04 } },
};

export const cardVariants: Variants = {
  hidden: { opacity: 0, y: 14, scale: 0.985 },
  shown: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { type: "spring", stiffness: 360, damping: 30 },
  },
};
