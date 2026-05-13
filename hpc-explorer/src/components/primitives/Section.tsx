import type { ReactNode } from "react";
import { motion } from "motion/react";
import { useReveal } from "@/hooks/useReveal";
import { cn } from "@/lib/cn";
import { EASE_SNAP } from "@/lib/motion";

interface SectionProps {
  index: number;
  id: string;
  title: ReactNode;
  subtitle?: ReactNode;
  rightSlot?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function Section({
  index,
  id,
  title,
  subtitle,
  rightSlot,
  children,
  className,
}: SectionProps) {
  const { ref, revealed } = useReveal<HTMLDivElement>();
  const num = index.toString().padStart(2, "0");

  return (
    <section
      id={id}
      ref={ref}
      tabIndex={-1}
      className={cn(
        "relative scroll-mt-40 px-3 sm:px-6 py-12 sm:py-16 outline-none",
        className,
      )}
    >
      <motion.header
        initial={{ opacity: 0, y: 14 }}
        animate={revealed ? { opacity: 1, y: 0 } : { opacity: 0, y: 14 }}
        transition={{ duration: 0.6, ease: EASE_SNAP }}
        className="flex items-end justify-between gap-4 mb-8 pb-3 border-b border-[var(--color-border-lo)]"
      >
        <div className="min-w-0">
          <div className="flex items-baseline gap-3">
            <span className="font-mono text-[var(--color-accent)] text-xs tracking-eyebrow">
              §&nbsp;{num}
            </span>
            <h2 className="font-display font-semibold text-2xl sm:text-3xl text-[var(--color-fg)] tracking-tight">
              {title}
            </h2>
          </div>
          {subtitle && (
            <p className="mt-1.5 text-sm text-[var(--color-fg-dim)] font-mono">
              {subtitle}
            </p>
          )}
        </div>
        {rightSlot && <div className="shrink-0">{rightSlot}</div>}
      </motion.header>

      <motion.div
        initial={{ opacity: 0, y: 18 }}
        animate={revealed ? { opacity: 1, y: 0 } : { opacity: 0, y: 18 }}
        transition={{ duration: 0.7, ease: EASE_SNAP, delay: 0.08 }}
      >
        {children}
      </motion.div>
    </section>
  );
}
