import type { GpuTopology, TopoLink } from "@/lib/types";
import { Tooltip } from "@/components/primitives/Tooltip";
import { TOPO_COLOR, TOPO_LABEL, shortNodeId } from "@/lib/topology";

interface NvLinkMiniProps {
  topology: GpuTopology;
}

const CELL_BG: Record<TopoLink, string> = {
  ...TOPO_COLOR,
  X: "var(--color-bg-deep)",
};

export function NvLinkMini({ topology }: NvLinkMiniProps) {
  const nodes = Object.keys(topology.matrix);
  const cells = nodes.length;
  const size = 14;

  return (
    <div className="panel p-2 inline-block">
      <div className="font-mono text-[9px] uppercase tracking-eyebrow text-[var(--color-fg-mute)] mb-1.5 px-0.5">
        nvlink topology
      </div>
      <div
        className="grid gap-px bg-[var(--color-border)]"
        style={{
          gridTemplateColumns: `${size + 2}px repeat(${cells}, ${size}px)`,
        }}
      >
        <div />
        {nodes.map((n) => (
          <div
            key={`hdr-${n}`}
            className="text-[8px] font-mono text-[var(--color-fg-mute)] flex items-end justify-center pb-px"
          >
            {shortNodeId(n)}
          </div>
        ))}
        {nodes.map((row) => (
          <RowGrouping key={`row-${row}`} row={row} nodes={nodes} matrix={topology.matrix} />
        ))}
      </div>
      <div className="mt-1.5 px-0.5 flex gap-2 font-mono text-[8px] text-[var(--color-fg-mute)]">
        <Swatch link="NV12" />
        <Swatch link="NODE" />
        <Swatch link="SYS" />
      </div>
    </div>
  );
}

function Swatch({ link }: { link: TopoLink }) {
  return (
    <span className="flex items-center gap-1">
      <span className="w-2 h-2 inline-block" style={{ background: CELL_BG[link] }} />
      {link}
    </span>
  );
}

function RowGrouping({
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
      <div className="text-[8px] font-mono text-[var(--color-fg-mute)] flex items-center justify-end pr-1">
        {shortNodeId(row)}
      </div>
      {nodes.map((col) => {
        const link = matrix[row]?.[col] ?? "X";
        return (
          <Tooltip
            key={`${row}-${col}`}
            content={
              <div>
                <div className="text-[var(--color-accent)]">{row} → {col}</div>
                <div className="text-[var(--color-fg-dim)]">{link}: {TOPO_LABEL[link]}</div>
              </div>
            }
          >
            <div
              className="cursor-pointer"
              style={{ background: CELL_BG[link] }}
              aria-label={`${row} ${col} ${link}`}
            />
          </Tooltip>
        );
      })}
    </>
  );
}
