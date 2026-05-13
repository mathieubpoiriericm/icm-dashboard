import { useMemo } from "react";
import { Sparkline } from "@/components/primitives/Sparkline";
import { Counter } from "@/components/primitives/Counter";

interface QueueWidgetProps {
  running: number;
  pending: number;
  total: number;
}

// Synthesize a plausible 30-tick history with noise around the current values
function synthHistory(target: number, n = 28): number[] {
  const out: number[] = [];
  for (let i = 0; i < n; i++) {
    const drift = Math.sin(i * 0.45) * 8 + Math.cos(i * 0.13) * 3;
    out.push(Math.max(0, Math.round(target + drift - 4 + i * 0.1)));
  }
  out.push(target);
  return out;
}

export function QueueWidget({ running, pending, total }: QueueWidgetProps) {
  const series = useMemo(() => synthHistory(running), [running]);

  return (
    <div className="panel p-2">
      <div className="flex items-center justify-between mb-1.5">
        <div className="font-mono text-[9px] uppercase tracking-eyebrow text-[var(--color-fg-mute)]">
          cluster queue
        </div>
        <div className="font-mono text-[9px] text-[var(--color-fg-mute)] tabular">
          {total} total
        </div>
      </div>
      <div className="flex items-end justify-between gap-3 mb-1">
        <div>
          <div className="font-mono text-[9px] text-[var(--color-fg-mute)] uppercase tracking-eyebrow">
            running
          </div>
          <div className="font-mono text-2xl text-[var(--color-good)] leading-none">
            <Counter to={running} />
          </div>
        </div>
        <Sparkline values={series} width={90} height={22} color="var(--color-good)" />
      </div>
      <div className="flex items-center justify-between font-mono text-[10px]">
        <span className="text-[var(--color-fg-mute)]">pending</span>
        <span className="text-[var(--color-warn)] tabular">{pending}</span>
      </div>
    </div>
  );
}
