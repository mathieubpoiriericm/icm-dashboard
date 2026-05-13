import { motion } from "motion/react";
import type { Fairshare } from "@/lib/types";
import { useReducedMotion } from "@/hooks/useReducedMotion";
import { EASE_SNAP } from "@/lib/motion";

interface FairshareGaugeProps {
  data: Fairshare;
}

export function FairshareGauge({ data }: FairshareGaugeProps) {
  const reduced = useReducedMotion();
  const safeFairshare = Number.isFinite(data.fairshare) ? data.fairshare : 0;
  const value = Math.max(0, Math.min(1, safeFairshare));
  const radius = 28;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - value);

  return (
    <div className="panel p-2 flex items-center gap-3">
      <div className="relative w-[72px] h-[72px] shrink-0">
        <svg viewBox="0 0 80 80" width={72} height={72} className="-rotate-90">
          <circle
            cx={40}
            cy={40}
            r={radius}
            fill="none"
            stroke="var(--color-border)"
            strokeWidth={4}
          />
          <motion.circle
            cx={40}
            cy={40}
            r={radius}
            fill="none"
            stroke="var(--color-accent)"
            strokeWidth={4}
            strokeLinecap="round"
            strokeDasharray={circumference}
            initial={reduced ? { strokeDashoffset: offset } : { strokeDashoffset: circumference }}
            animate={{ strokeDashoffset: offset }}
            transition={{ duration: 1.2, ease: EASE_SNAP, delay: 0.2 }}
            style={{
              filter: "drop-shadow(0 0 4px color-mix(in oklab, var(--color-accent) 60%, transparent))",
            }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center font-mono">
          <div className="text-[8px] uppercase tracking-eyebrow text-[var(--color-fg-mute)]">
            fair
          </div>
          <div className="text-[var(--color-accent)] tabular text-sm leading-none">
            {safeFairshare.toFixed(3)}
          </div>
        </div>
      </div>
      <div className="font-mono text-[10px] space-y-0.5 min-w-0">
        <div className="text-[var(--color-fg-mute)] uppercase tracking-eyebrow text-[9px]">
          fairshare
        </div>
        <div className="text-[var(--color-fg-dim)] truncate">
          <span className="text-[var(--color-fg-mute)]">user</span>{" "}
          <span className="text-[var(--color-fg)]">{data.user}</span>
        </div>
        <div className="text-[var(--color-fg-dim)] tabular">
          <span className="text-[var(--color-fg-mute)]">raw</span> {data.rawUsage}
        </div>
        <div className="text-[var(--color-fg-dim)] tabular">
          <span className="text-[var(--color-fg-mute)]">eff</span> {data.effUsagePct}%
        </div>
      </div>
    </div>
  );
}
