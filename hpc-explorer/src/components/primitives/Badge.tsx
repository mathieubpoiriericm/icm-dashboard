import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

type BadgeVariant = "accent" | "good" | "warn" | "crit" | "dim" | "outline";

interface BadgeProps {
  variant?: BadgeVariant;
  children: ReactNode;
  pulse?: boolean;
  size?: "xs" | "sm";
  className?: string;
  title?: string;
}

const VARIANT: Record<BadgeVariant, string> = {
  accent:
    "border-[var(--color-accent)]/40 text-[var(--color-accent)] bg-[var(--color-accent-deep)]/30",
  good:
    "border-[var(--color-good)]/40 text-[var(--color-good)] bg-[var(--color-good-lo)]/30",
  warn:
    "border-[var(--color-warn)]/40 text-[var(--color-warn)] bg-[var(--color-warn-lo)]/30",
  crit:
    "border-[var(--color-crit)]/40 text-[var(--color-crit)] bg-[var(--color-crit-lo)]/30",
  dim:
    "border-[var(--color-border)] text-[var(--color-fg-dim)] bg-[var(--color-bg-elev)]",
  outline:
    "border-[var(--color-border-hi)] text-[var(--color-fg)] bg-transparent",
};

const SIZE: Record<NonNullable<BadgeProps["size"]>, string> = {
  xs: "px-1.5 py-0 text-[9px]",
  sm: "px-2 py-0.5 text-[10px]",
};

export function Badge({
  variant = "dim",
  children,
  pulse = false,
  size = "sm",
  className,
  title,
}: BadgeProps) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1 font-mono uppercase tracking-eyebrow border rounded-sm",
        SIZE[size],
        VARIANT[variant],
        className,
      )}
    >
      {pulse && (
        <span
          className="w-1 h-1 rounded-full bg-current"
          style={{ animation: "blink 1.4s ease-in-out infinite" }}
        />
      )}
      {children}
    </span>
  );
}
