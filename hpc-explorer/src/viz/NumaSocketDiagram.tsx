import { motion } from "motion/react";
import type { Cpu, GpuTopology } from "@/lib/types";
import { cn } from "@/lib/cn";
import { EASE_SNAP, STAGGER, STAGGER_GROUP } from "@/lib/motion";
import { numaOccupants } from "@/lib/topology";

interface NumaSocketDiagramProps {
  cpu: Cpu;
  topology: GpuTopology;
}

interface NumaInfo {
  index: number;
  socket: number;
  coreRange: string;
  occupants: string[];
  empty: boolean;
}

export function NumaSocketDiagram({ cpu, topology }: NumaSocketDiagramProps) {
  const occupants = numaOccupants(topology);
  if (cpu.sockets <= 0 || cpu.numaNodes <= 0) {
    return (
      <div className="panel p-4 font-mono text-[11px] text-[var(--color-fg-mute)]">
        CPU topology unavailable
      </div>
    );
  }
  const numasPerSocket = cpu.numaNodes / cpu.sockets;
  const coresPerNuma =
    numasPerSocket > 0 ? cpu.coresPerSocket / numasPerSocket : 0;

  const numaList: NumaInfo[] = Array.from({ length: cpu.numaNodes }, (_, i) => {
    const startCore = i * coresPerNuma;
    const endCore = startCore + coresPerNuma - 1;
    const occ = occupants[i] ?? [];
    return {
      index: i,
      socket: Math.floor(i / numasPerSocket),
      coreRange: `${startCore}-${endCore}`,
      occupants: occ,
      empty: occ.length === 0,
    };
  });

  return (
    <div className="space-y-4">
      {Array.from({ length: cpu.sockets }, (_, socket) => {
        const numasInSocket = numaList.filter((n) => n.socket === socket);
        return (
          <div
            key={socket}
            className="panel panel-corners p-4 bg-gradient-to-b from-[var(--color-bg-panel)] to-[var(--color-bg-elev)]"
          >
            <div className="flex items-center justify-between mb-3">
              <div className="font-mono text-[10px] uppercase tracking-eyebrow text-[var(--color-fg-mute)]">
                Socket {socket}
              </div>
              <div className="font-mono text-[10px] text-[var(--color-fg-dim)]">
                {cpu.coresPerSocket} cores · {cpu.coresPerSocket * cpu.threadsPerCore} threads
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              {numasInSocket.map((n, idx) => (
                <NumaBlock
                  key={n.index}
                  info={n}
                  coresPerNuma={coresPerNuma}
                  delay={socket * STAGGER_GROUP + idx * STAGGER}
                />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function NumaBlock({
  info,
  coresPerNuma,
  delay,
}: {
  info: NumaInfo;
  coresPerNuma: number;
  delay: number;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "0px 0px -10% 0px" }}
      transition={{ duration: 0.5, delay, ease: EASE_SNAP }}
      className={cn(
        "p-3 border relative",
        info.empty
          ? "border-dashed border-[var(--color-border-lo)] bg-[var(--color-bg-deep)]/40"
          : "border-[var(--color-accent)]/40 bg-[var(--color-accent-deep)]/15",
      )}
    >
      <div className="flex items-baseline justify-between mb-2">
        <div
          className={cn(
            "font-mono text-xs uppercase tracking-eyebrow",
            info.empty ? "text-[var(--color-fg-mute)]" : "text-[var(--color-accent)]",
          )}
        >
          numa {info.index}
        </div>
        <div className="font-mono text-[10px] text-[var(--color-fg-mute)] tabular">
          cores {info.coreRange}
        </div>
      </div>

      <div
        className="grid gap-px mb-3"
        style={{ gridTemplateColumns: `repeat(${coresPerNuma}, 1fr)` }}
      >
        {Array.from({ length: coresPerNuma }).map((_, i) => (
          <span
            key={i}
            className={cn(
              "h-1.5 inline-block",
              info.empty
                ? "bg-[var(--color-border-lo)]"
                : "bg-[var(--color-accent)]/50",
            )}
          />
        ))}
      </div>

      {info.empty ? (
        <div className="font-mono text-[10px] text-[var(--color-fg-mute)] italic">
          unpopulated — no GPU, no NIC
        </div>
      ) : (
        <div className="flex flex-wrap gap-1">
          {info.occupants.map((occ) => (
            <span
              key={occ}
              className={cn(
                "px-1.5 py-0.5 font-mono text-[10px] border tabular",
                occ.startsWith("NIC")
                  ? "border-[var(--color-warn)]/40 text-[var(--color-warn)] bg-[var(--color-warn-lo)]/30"
                  : "border-[var(--color-accent)]/40 text-[var(--color-accent)] bg-[var(--color-accent-deep)]/30",
              )}
            >
              {occ}
            </span>
          ))}
        </div>
      )}
    </motion.div>
  );
}
