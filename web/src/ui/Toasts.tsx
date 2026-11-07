import { AnimatePresence, motion } from "motion/react";
import { CheckCircle2, Info, TriangleAlert } from "lucide-react";
import { useCallback, useMemo, useRef, useState, type ReactNode } from "react";

import { type Notify, ToastContext, type ToastTone } from "./toast";
import styles from "./Toasts.module.css";

type Toast = { id: number; message: string; tone: ToastTone };

const ICONS = { success: CheckCircle2, info: Info, error: TriangleAlert };

/** Brief, polite notices (role="status"). */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(0);
  const notify = useCallback<Notify>((message, tone = "success") => {
    next.current += 1;
    const id = next.current;
    setToasts((current) => [...current.slice(-3), { id, message, tone }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id));
    }, 4200);
  }, []);
  const value = useMemo(() => notify, [notify]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className={styles.region} role="status" aria-live="polite">
        <AnimatePresence initial={false}>
          {toasts.map((toast) => {
            const Icon = ICONS[toast.tone];
            return (
              <motion.div
                key={toast.id}
                layout
                className={`${styles.toast} ${styles[toast.tone]}`}
                initial={{ opacity: 0, y: 16, scale: 0.96 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 8, scale: 0.98, transition: { duration: 0.18 } }}
                transition={{ type: "spring", stiffness: 420, damping: 32 }}
              >
                <Icon aria-hidden="true" size={18} />
                <span>{toast.message}</span>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}
