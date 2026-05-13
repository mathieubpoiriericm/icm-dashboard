import { AnimatePresence, motion } from "motion/react";
import { type ChordTarget, formatChordTarget } from "@/hooks/useSectionNav";

interface ChordNavPillProps {
  target: ChordTarget | null;
}

export function ChordNavPill({ target }: ChordNavPillProps) {
  const label = target ? formatChordTarget(target) : null;
  return (
    <AnimatePresence>
      {label && (
        <motion.div
          key={label}
          role="status"
          aria-live="polite"
          initial={{ opacity: 0, y: -6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.18 }}
          className="fixed top-3 left-1/2 -translate-x-1/2 z-50 panel panel-corners px-3 py-1.5 font-mono text-[10px] uppercase tracking-eyebrow text-[var(--color-accent)] bg-[var(--color-bg-deep)]/90 pointer-events-none"
        >
          <span aria-hidden="true">→ </span>
          {label}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
