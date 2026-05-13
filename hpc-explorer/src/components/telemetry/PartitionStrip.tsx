import type { Partition } from "@/lib/types";
import { Bar } from "@/components/primitives/Bar";

interface PartitionStripProps {
  partitions: Partition[];
  highlight?: string;
  max?: number;
}

export function PartitionStrip({ partitions, highlight, max = 5 }: PartitionStripProps) {
  // Show the most-utilized partitions first, but pin highlight to top
  const sorted = [...partitions].sort((a, b) => {
    if (a.name === highlight) return -1;
    if (b.name === highlight) return 1;
    return b.cpuUsePct - a.cpuUsePct;
  });
  const visible = sorted.slice(0, max);

  return (
    <div className="panel p-2">
      <div className="font-mono text-[9px] uppercase tracking-eyebrow text-[var(--color-fg-mute)] mb-1.5">
        partition load
      </div>
      <div className="space-y-1">
        {visible.map((p) => {
          const isActive = p.name === highlight;
          return (
            <div key={p.name} className="grid grid-cols-[7rem_1fr_2rem] items-center gap-2">
              <div
                className="font-mono text-[10px] truncate"
                title={`${p.gres || "—"} · ${p.cpus.alloc}/${p.cpus.total} CPU`}
              >
                <span className={isActive ? "text-[var(--color-accent)]" : "text-[var(--color-fg-dim)]"}>
                  {p.name}
                </span>
              </div>
              <Bar value={p.cpuUsePct} variant="auto" size="xs" animate={false} />
              <div className="font-mono text-[9px] text-[var(--color-fg-mute)] tabular text-right">
                {p.cpuUsePct}%
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
