import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

interface KVProps {
  k: ReactNode;
  v: ReactNode;
  hint?: ReactNode;
  variant?: "default" | "good" | "warn" | "crit" | "accent";
  truncate?: boolean;
  className?: string;
}

const VARIANT_COLOR: Record<NonNullable<KVProps["variant"]>, string> = {
  default: "text-[var(--color-fg)]",
  good: "text-[var(--color-good)]",
  warn: "text-[var(--color-warn)]",
  crit: "text-[var(--color-crit)]",
  accent: "text-[var(--color-accent)]",
};

export function KV({
  k,
  v,
  hint,
  variant = "default",
  truncate = false,
  className,
}: KVProps) {
  return (
    <div
      className={cn(
        "flex items-baseline gap-2 font-mono text-xs leading-relaxed",
        className,
      )}
    >
      <span className="text-[var(--color-fg-mute)] uppercase tracking-eyebrow text-[10px] shrink-0">
        {k}
      </span>
      <span className="grow border-b border-dotted border-[var(--color-border-lo)] mx-1" />
      <span
        className={cn(VARIANT_COLOR[variant], truncate && "truncate min-w-0", "tabular")}
      >
        {v}
      </span>
      {hint && (
        <span className="text-[var(--color-fg-mute)] text-[10px]">{hint}</span>
      )}
    </div>
  );
}
