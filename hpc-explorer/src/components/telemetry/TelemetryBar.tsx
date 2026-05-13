import { useMemo, type ReactNode } from "react";
import { motion } from "motion/react";
import type { ClusterSnapshot } from "@/lib/types";
import { IdentityStrip } from "./IdentityStrip";
import { GpuCard } from "./GpuCard";
import { NvLinkMini } from "./NvLinkMini";
import { NumaStrip } from "./NumaStrip";
import { QueueWidget } from "./QueueWidget";
import { PartitionStrip } from "./PartitionStrip";
import { FairshareGauge } from "./FairshareGauge";
import { gpuPciSlots } from "@/lib/topology";
import { useBootSequence } from "@/hooks/useBootSequence";
import { EASE_SNAP, STAGGER } from "@/lib/motion";

interface TelemetryBarProps {
  data: ClusterSnapshot;
}

function BootStage({
  active,
  className,
  children,
}: {
  active: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: -4 }}
      animate={active ? { opacity: 1, y: 0 } : { opacity: 0, y: -4 }}
      transition={{ duration: 0.4, ease: EASE_SNAP }}
      className={className}
    >
      {children}
    </motion.div>
  );
}

export function TelemetryBar({ data }: TelemetryBarProps) {
  const slots = useMemo(() => gpuPciSlots(data.pciHCAs), [data.pciHCAs]);
  const nvlinkByGpu = useMemo(() => {
    const m = new Map<number, number>();
    for (const s of data.nvlinkStatus) m.set(s.gpu, s.links);
    return m;
  }, [data.nvlinkStatus]);

  const stage = useBootSequence(3, 140);

  return (
    <div className="sticky top-0 z-40 backdrop-blur-md bg-[var(--color-bg)]/85 border-b border-[var(--color-border)]">
      <IdentityStrip
        cluster={data.meta.cluster}
        host={data.meta.host}
        jobId={data.meta.jobId}
        partition={data.slurmJobContext.partition}
        qos={data.slurmJobContext.qos}
        account={data.meta.account}
        probedAt={data.meta.probedAt}
        uptime={data.computeHost.uptime}
        jobState={data.slurmNodeView.state}
      />
      <BootStage
        active={stage >= 1}
        className="px-3 py-2.5 grid grid-cols-2 md:grid-cols-4 xl:grid-cols-12 gap-2"
      >
        {data.gpus.map((g, i) => (
          <div key={g.idx} className="col-span-1 md:col-span-1 xl:col-span-2">
            <GpuCard
              idx={g.idx}
              name={g.name}
              memUsed={g.memUsed}
              memTotal={g.memTotal}
              capability={g.capability}
              bf16={data.pytorchCuda.bf16}
              nvLinks={nvlinkByGpu.get(g.idx) ?? 0}
              pciSlot={slots[`GPU${g.idx}`]}
              numa={data.gpuTopology.numaAffinity[`GPU${g.idx}`] ?? null}
              delay={0.05 + i * STAGGER}
            />
          </div>
        ))}

        <div className="col-span-2 md:col-span-2 xl:col-span-2">
          <NvLinkMini topology={data.gpuTopology} />
        </div>
        <div className="col-span-2 md:col-span-2 xl:col-span-2">
          <FairshareGauge data={data.fairshare} />
        </div>
      </BootStage>

      <BootStage
        active={stage >= 2}
        className="px-3 pb-2.5 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-12 gap-2"
      >
        <div className="md:col-span-2 xl:col-span-5">
          <NumaStrip topology={data.gpuTopology} numaCount={data.cpu.numaNodes} />
        </div>
        <div className="md:col-span-1 xl:col-span-3">
          <QueueWidget
            running={data.clusterQueue.running}
            pending={data.clusterQueue.pending}
            total={data.clusterQueue.total}
          />
        </div>
        <div className="md:col-span-1 xl:col-span-4">
          <PartitionStrip
            partitions={data.partitions}
            highlight={data.slurmJobContext.partition}
          />
        </div>
      </BootStage>
    </div>
  );
}
