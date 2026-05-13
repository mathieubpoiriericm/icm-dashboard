import type { GpuTopology } from "@/lib/types";
import { cn } from "@/lib/cn";
import { numaOccupants, shortNodeId } from "@/lib/topology";

interface NumaStripProps {
  topology: GpuTopology;
  numaCount: number;
}

export function NumaStrip({ topology, numaCount }: NumaStripProps) {
  const occupants = numaOccupants(topology);

  return (
    <div className="panel p-2">
      <div className="font-mono text-[9px] uppercase tracking-eyebrow text-[var(--color-fg-mute)] mb-1.5">
        numa domains
      </div>
      <div className="flex gap-1">
        {Array.from({ length: numaCount }).map((_, i) => {
          const occ = occupants[i] ?? [];
          const empty = occ.length === 0;
          return (
            <div
              key={i}
              className={cn(
                "flex-1 px-2 py-1.5 border font-mono text-[9px] text-center transition-colors",
                empty
                  ? "border-dashed border-[var(--color-border-lo)] text-[var(--color-fg-mute)]"
                  : "border-[var(--color-accent)]/40 bg-[var(--color-accent-deep)]/30 text-[var(--color-accent)]",
              )}
              title={
                empty
                  ? `NUMA ${i} — unpopulated`
                  : `NUMA ${i} hosts ${occ.join(", ")}`
              }
            >
              <div className="text-[var(--color-fg-mute)] tracking-eyebrow">N{i}</div>
              <div className="tabular text-[10px] mt-0.5">
                {empty ? "—" : occ.map(shortNodeId).join(" ")}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
