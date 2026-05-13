import type { ReactNode, HTMLAttributes } from "react";
import { cn } from "@/lib/cn";

interface PanelProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  title?: ReactNode;
  subtitle?: ReactNode;
  chrome?: ReactNode;
  rightSlot?: ReactNode;
  corners?: boolean;
  glow?: "none" | "accent" | "warn" | "crit";
  density?: "compact" | "normal";
}

export function Panel({
  title,
  subtitle,
  chrome,
  rightSlot,
  corners = true,
  glow = "none",
  density = "normal",
  className,
  children,
  ...rest
}: PanelProps) {
  const glowMap = {
    none: "",
    accent:
      "shadow-[0_0_0_1px_color-mix(in_oklab,var(--color-accent)_40%,transparent),0_0_22px_-4px_color-mix(in_oklab,var(--color-accent)_60%,transparent)]",
    warn:
      "shadow-[0_0_0_1px_color-mix(in_oklab,var(--color-warn)_40%,transparent),0_0_22px_-4px_color-mix(in_oklab,var(--color-warn)_60%,transparent)]",
    crit:
      "shadow-[0_0_0_1px_color-mix(in_oklab,var(--color-crit)_40%,transparent),0_0_22px_-4px_color-mix(in_oklab,var(--color-crit)_60%,transparent)]",
  };

  return (
    <div
      {...rest}
      className={cn(
        "panel",
        corners && "panel-corners",
        glow !== "none" && glowMap[glow],
        density === "compact" ? "p-3" : "p-4",
        className,
      )}
    >
      {(title || chrome || rightSlot) && (
        <header className="flex items-start justify-between gap-3 mb-3 pb-2 border-b border-[var(--color-border-lo)]">
          <div className="min-w-0 flex-1">
            {title && (
              <h3 className="text-[10px] uppercase tracking-eyebrow text-[var(--color-fg-dim)] font-mono font-medium">
                {title}
              </h3>
            )}
            {subtitle && (
              <p className="mt-0.5 text-xs text-[var(--color-fg-mute)] font-mono truncate">
                {subtitle}
              </p>
            )}
          </div>
          {chrome && <div className="text-xs text-[var(--color-fg-mute)] font-mono">{chrome}</div>}
          {rightSlot}
        </header>
      )}
      {children}
    </div>
  );
}
