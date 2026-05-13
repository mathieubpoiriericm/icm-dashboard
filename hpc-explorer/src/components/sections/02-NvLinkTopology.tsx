import { useMemo } from "react";
import type { ClusterSnapshot, TopoLink } from "@/lib/types";
import { Section } from "@/components/primitives/Section";
import { Panel } from "@/components/primitives/Panel";
import { Badge } from "@/components/primitives/Badge";
import { NvLinkForceGraph } from "@/viz/NvLinkForceGraph";
import { TOPO_COLOR, gpuPciSlots } from "@/lib/topology";

interface NvLinkSectionProps {
  data: ClusterSnapshot;
}

const CELL_BG: Record<TopoLink, string> = {
  ...TOPO_COLOR,
  X: "var(--color-bg-deep)",
};

const CELL_FG: Record<TopoLink, string> = {
  X: "var(--color-fg-mute)",
  NV12: "var(--color-bg-deep)",
  NODE: "var(--color-bg-deep)",
  SYS: "var(--color-fg-dim)",
  PHB: "var(--color-bg)",
  PXB: "var(--color-bg)",
  PIX: "var(--color-bg-deep)",
};

export function NvLinkTopologySection({ data }: NvLinkSectionProps) {
  const slots = useMemo(() => gpuPciSlots(data.pciHCAs), [data.pciHCAs]);

  const totalNVLinkBw =
    (data.nvlinkStatus[0]?.links ?? 0) * (data.nvlinkStatus[0]?.speedGBs ?? 0);

  return (
    <Section
      index={2}
      id="nvlink"
      title="NVLink topology"
      subtitle="Force-laid graph of GPU↔GPU and GPU↔NIC fabric. NV12 cyan dashes flow between bonded pairs; amber NODE link puts NIC0 on the same NUMA bridge as GPU1; SYS edges cross sockets via PCIe + UPI."
      rightSlot={
        <div className="flex items-center gap-2">
          <Badge variant="accent">A100 PCIe 80GB × 4</Badge>
          <Badge variant="dim">{totalNVLinkBw} GB/s per dir</Badge>
        </div>
      }
    >
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="xl:col-span-2">
          <Panel title="topology graph" corners glow="accent">
            <NvLinkForceGraph
              topology={data.gpuTopology}
              nvlinkPerGpu={data.nvlinkStatus[0]?.links ?? 12}
              nvlinkSpeedGBs={data.nvlinkStatus[0]?.speedGBs ?? 25}
              pciSlots={slots}
              width={680}
              height={420}
            />
          </Panel>
        </div>

        <div className="space-y-4">
          <Panel title="connectivity matrix" corners>
            <Matrix data={data} />
          </Panel>

          <Panel title="story">
            <ul className="space-y-2 font-mono text-xs text-[var(--color-fg-dim)]">
              <Bullet>
                GPUs <span className="text-[var(--color-accent)]">0 ↔ 1</span> share
                an NV12 bond <span className="text-[var(--color-fg-mute)]">across</span>{" "}
                NUMA (0 vs 1).
              </Bullet>
              <Bullet>
                GPUs <span className="text-[var(--color-accent)]">2 ↔ 3</span> share
                an NV12 bond <span className="text-[var(--color-fg-mute)]">within</span>{" "}
                NUMA 3 (cores 42–55).
              </Bullet>
              <Bullet>
                <span className="text-[var(--color-warn)]">NIC0</span> (Mellanox
                ConnectX-6, mlx5_0) is NODE-linked to GPU1 — they share NUMA 1's host
                bridge. The only intra-host non-SYS path to the NIC.
              </Bullet>
              <Bullet>
                Cross-pair traffic (0,1 ↔ 2,3) traverses PCIe + UPI — that's SYS.
              </Bullet>
            </ul>
          </Panel>
        </div>
      </div>
    </Section>
  );
}

function Bullet({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex gap-2">
      <span className="text-[var(--color-accent)]">›</span>
      <span className="flex-1">{children}</span>
    </li>
  );
}

function Matrix({ data }: { data: ClusterSnapshot }) {
  const nodes = Object.keys(data.gpuTopology.matrix);
  return (
    <div className="font-mono">
      <div
        className="grid gap-px bg-[var(--color-border)] p-px"
        style={{ gridTemplateColumns: `48px repeat(${nodes.length}, 1fr)` }}
      >
        <div />
        {nodes.map((n) => (
          <div
            key={`hh-${n}`}
            className="text-[10px] text-[var(--color-fg-mute)] flex items-center justify-center py-1"
          >
            {n}
          </div>
        ))}
        {nodes.map((row) => (
          <RowMatrix key={`r-${row}`} row={row} nodes={nodes} matrix={data.gpuTopology.matrix} />
        ))}
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 text-[10px]">
        {(["NV12", "NODE", "SYS"] as TopoLink[]).map((t) => (
          <div key={t} className="flex items-center gap-2">
            <span
              className="inline-block w-3 h-3 border"
              style={{
                background: CELL_BG[t],
                borderColor: "var(--color-border-hi)",
              }}
            />
            <span className="text-[var(--color-fg-dim)]">{t}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function RowMatrix({
  row,
  nodes,
  matrix,
}: {
  row: string;
  nodes: string[];
  matrix: Record<string, Record<string, TopoLink>>;
}) {
  return (
    <>
      <div className="text-[10px] text-[var(--color-fg-mute)] flex items-center justify-end pr-2">
        {row}
      </div>
      {nodes.map((col) => {
        const link = matrix[row]?.[col] ?? "X";
        return (
          <div
            key={`c-${row}-${col}`}
            className="aspect-square flex items-center justify-center text-[9px] tabular"
            style={{ background: CELL_BG[link], color: CELL_FG[link] }}
          >
            {link}
          </div>
        );
      })}
    </>
  );
}
