import { motion } from "motion/react";
import { clamp } from "@/lib/format";
import { cn } from "@/lib/cn";
import { EASE_SNAP } from "@/lib/motion";

interface BarProps {
  value: number;
  max?: number;
  variant?: "accent" | "good" | "warn" | "crit" | "auto";
  size?: "xs" | "sm" | "md";
  showValue?: boolean;
  label?: string;
  className?: string;
  animate?: boolean;
}

const VARIANT_COLOR: Record<Exclude<BarProps["variant"], "auto" | undefined>, string> = {
  accent: "var(--color-accent)",
  good: "var(--color-good)",
  warn: "var(--color-warn)",
  crit: "var(--color-crit)",
};

const SIZE: Record<NonNullable<BarProps["size"]>, string> = {
  xs: "h-1",
  sm: "h-1.5",
  md: "h-2",
};

export function Bar({
  value,
  max = 100,
  variant = "accent",
  size = "sm",
  showValue = false,
  label,
  className,
  animate = true,
}: BarProps) {
  const pct = clamp((value / max) * 100, 0, 100);
  const resolved =
    variant === "auto"
      ? pct >= 90
        ? "crit"
        : pct >= 70
          ? "warn"
          : "accent"
      : variant;
  const color = VARIANT_COLOR[resolved];

  return (
    <div className={cn("w-full", className)}>
      {(label || showValue) && (
        <div className="flex items-baseline justify-between mb-1 font-mono text-[10px] uppercase tracking-eyebrow text-[var(--color-fg-mute)]">
          {label && <span>{label}</span>}
          {showValue && (
            <span className="tabular text-[var(--color-fg-dim)]">{pct.toFixed(0)}%</span>
          )}
        </div>
      )}
      <div
        className={cn(
          "w-full bg-[var(--color-bg-deep)] border border-[var(--color-border-lo)] overflow-hidden relative",
          SIZE[size],
        )}
      >
        <motion.div
          className="h-full"
          style={{
            background: `linear-gradient(90deg, ${color} 0%, color-mix(in oklab, ${color} 60%, transparent) 100%)`,
            boxShadow: `0 0 8px -2px ${color}`,
          }}
          initial={animate ? { width: 0 } : { width: `${pct}%` }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 1.0, ease: EASE_SNAP }}
        />
        <div
          className="absolute inset-y-0 left-0 pointer-events-none mix-blend-overlay"
          style={{
            background:
              "repeating-linear-gradient(90deg, transparent 0 4px, rgba(255,255,255,.04) 4px 5px)",
            width: `${pct}%`,
          }}
        />
      </div>
    </div>
  );
}
