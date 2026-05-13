import { motion } from "motion/react";
import { Bar } from "@/components/primitives/Bar";
import { Badge } from "@/components/primitives/Badge";
import { cn } from "@/lib/cn";
import { parseMemoryGB } from "@/lib/format";
import { EASE_SNAP } from "@/lib/motion";

interface GpuCardProps {
  idx: number;
  name: string;
  memUsed: string;
  memTotal: string;
  capability: string;
  bf16: boolean;
  nvLinks: number;
  pciSlot?: string;
  numa?: number | null;
  delay?: number;
}

export function GpuCard({
  idx,
  name,
  memUsed,
  memTotal,
  capability,
  bf16,
  nvLinks,
  pciSlot,
  numa,
  delay = 0,
}: GpuCardProps) {
  const memUsedGB = parseMemoryGB(memUsed);
  const memTotalGB = parseMemoryGB(memTotal);
  const usedPct = memTotalGB > 0 ? (memUsedGB / memTotalGB) * 100 : 0;
  const shortName = name.replace("NVIDIA ", "").replace(" PCIe", "");

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay, ease: EASE_SNAP }}
      className={cn(
        "panel panel-corners p-2.5 min-w-0",
        "bg-gradient-to-b from-[var(--color-bg-panel)] to-[var(--color-bg-elev)]",
        "hover:shadow-[0_0_18px_-4px_color-mix(in_oklab,var(--color-accent)_45%,transparent)]",
        "transition-shadow duration-300",
      )}
      role="group"
      aria-label={`GPU ${idx} — ${name}`}
    >
      <div className="flex items-center justify-between mb-2">
        <div className="font-mono text-[10px] uppercase tracking-eyebrow text-[var(--color-fg-mute)]">
          gpu_{idx}
        </div>
        <div className="flex gap-1">
          {bf16 && <Badge variant="good" size="xs" title="BFloat16 supported">bf16</Badge>}
          <Badge variant="accent" size="xs" title="Compute capability">cc {capability}</Badge>
        </div>
      </div>
      <div className="font-mono text-xs text-[var(--color-fg)] mb-1 truncate">
        {shortName}
      </div>
      <div className="font-mono text-[10px] text-[var(--color-fg-mute)] mb-2 tabular">
        {memUsed} / {memTotal}
      </div>
      <Bar value={usedPct} variant="accent" size="xs" animate />
      <div className="mt-2 flex items-center justify-between font-mono text-[10px] text-[var(--color-fg-mute)]">
        <span>
          NVLink <span className="text-[var(--color-accent)] tabular">×{nvLinks}</span>
        </span>
        {pciSlot && (
          <span className="text-[var(--color-fg-dim)] tabular">{pciSlot}</span>
        )}
        {typeof numa === "number" && (
          <span title={`NUMA domain ${numa}`}>
            <span className="text-[var(--color-fg-mute)]">numa</span>{" "}
            <span className="text-[var(--color-fg-dim)] tabular">{numa}</span>
          </span>
        )}
      </div>
    </motion.div>
  );
}
