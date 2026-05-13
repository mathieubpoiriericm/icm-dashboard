import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  forceSimulation,
  forceManyBody,
  forceLink,
  forceCenter,
  forceCollide,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from "d3-force";
import { motion, AnimatePresence } from "motion/react";
import type { GpuTopology, TopoLink } from "@/lib/types";
import { cn } from "@/lib/cn";
import { TOPO_COLOR, TOPO_LABEL } from "@/lib/topology";

interface Node extends SimulationNodeDatum {
  id: string;
  kind: "gpu" | "nic";
  pciSlot?: string;
  numa?: number | null;
}
interface Link extends SimulationLinkDatum<Node> {
  source: string | Node;
  target: string | Node;
  type: TopoLink;
  key: string;
}

interface NvLinkForceGraphProps {
  topology: GpuTopology;
  nvlinkPerGpu: number;
  nvlinkSpeedGBs: number;
  pciSlots: Record<string, string>;
  width?: number;
  height?: number;
}

const LINE_COLOR: Record<TopoLink, string> = {
  ...TOPO_COLOR,
  X: "transparent",
};

const STROKE_WIDTH: Record<TopoLink, number> = {
  X: 0,
  NV12: 3,
  NODE: 2,
  SYS: 1,
  PHB: 1.5,
  PXB: 1.5,
  PIX: 2,
};

function describeLink(t: TopoLink, nvLinks: number, speed: number): string {
  if (t === "NV12") {
    return `${nvLinks} NVLinks × ${speed} GB/s = ${nvLinks * speed} GB/s per direction (${nvLinks * speed * 2} GB/s bidirectional)`;
  }
  return TOPO_LABEL[t];
}

const SEED_X: Record<string, number> = {
  GPU0: 0.28,
  GPU1: 0.42,
  GPU2: 0.58,
  GPU3: 0.72,
};

export function NvLinkForceGraph({
  topology,
  nvlinkPerGpu,
  nvlinkSpeedGBs,
  pciSlots,
  width = 640,
  height = 380,
}: NvLinkForceGraphProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hoverEdge, setHoverEdge] = useState<{
    source: string;
    target: string;
    type: TopoLink;
    x: number;
    y: number;
  } | null>(null);
  const [, tick] = useReducer((x: number) => x + 1, 0);

  const { nodes, links } = useMemo(() => {
    const ids = Object.keys(topology.matrix);
    const nodes: Node[] = ids.map((id) => ({
      id,
      kind: id.startsWith("NIC") ? "nic" : "gpu",
      pciSlot: pciSlots[id],
      numa: topology.numaAffinity[id] ?? null,
      x: width * (SEED_X[id] ?? 0.5),
      y: height * (id.startsWith("GPU") ? 0.35 : 0.78),
      vx: 0,
      vy: 0,
    }));

    const links: Link[] = [];
    const seen = new Set<string>();
    for (const [a, row] of Object.entries(topology.matrix)) {
      for (const [b, t] of Object.entries(row)) {
        if (a === b) continue;
        if (t === "X") continue;
        const key = [a, b].sort().join("→");
        if (seen.has(key)) continue;
        seen.add(key);
        links.push({ source: a, target: b, type: t as TopoLink, key });
      }
    }
    return { nodes, links };
  }, [topology, pciSlots, width, height]);

  // Run a short force simulation to settle slightly
  useEffect(() => {
    const sim = forceSimulation(nodes)
      .force(
        "link",
        forceLink<Node, Link>(links)
          .id((d) => d.id)
          .distance((d) => (d.type === "NV12" ? 110 : d.type === "NODE" ? 95 : 165))
          .strength((d) => (d.type === "NV12" ? 1.2 : d.type === "NODE" ? 0.8 : 0.18)),
      )
      .force("charge", forceManyBody().strength(-380))
      .force("center", forceCenter(width / 2, height / 2).strength(0.06))
      .force("collide", forceCollide(46))
      .alphaMin(0.04)
      .alphaDecay(0.05);

    sim.on("tick", tick);
    return () => {
      sim.stop();
    };
  }, [nodes, links, width, height]);

  const isolated = (id: string): boolean => {
    if (!selected) return false;
    if (id === selected) return false;
    return !links.some((l) => {
      const s = typeof l.source === "string" ? l.source : l.source.id;
      const t = typeof l.target === "string" ? l.target : l.target.id;
      return (s === selected && t === id) || (t === selected && s === id);
    });
  };

  const linkDimmed = (l: Link): boolean => {
    if (!selected) return l.type === "SYS";
    const s = typeof l.source === "string" ? l.source : l.source.id;
    const t = typeof l.target === "string" ? l.target : l.target.id;
    return s !== selected && t !== selected;
  };

  return (
    <div className="relative">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
        width="100%"
        className="block"
        onClick={(e) => {
          if (e.target === svgRef.current) setSelected(null);
        }}
      >
        <defs>
          <marker
            id="arrow"
            viewBox="0 -5 10 10"
            refX={5}
            markerWidth={5}
            markerHeight={5}
            orient="auto"
          >
            <path d="M0,-4L8,0L0,4" fill="var(--color-accent)" />
          </marker>
          <radialGradient id="gpu-ring" cx="50%" cy="50%" r="50%">
            <stop offset="60%" stopColor="var(--color-accent-deep)" stopOpacity="0.4" />
            <stop offset="100%" stopColor="transparent" />
          </radialGradient>
        </defs>

        {/* gridlines */}
        <g opacity={0.5}>
          {Array.from({ length: 12 }).map((_, i) => (
            <line
              key={`vg${i}`}
              x1={(width / 12) * i}
              x2={(width / 12) * i}
              y1={0}
              y2={height}
              stroke="var(--color-grid)"
            />
          ))}
          {Array.from({ length: 6 }).map((_, i) => (
            <line
              key={`hg${i}`}
              x1={0}
              x2={width}
              y1={(height / 6) * i}
              y2={(height / 6) * i}
              stroke="var(--color-grid)"
            />
          ))}
        </g>

        {/* edges */}
        <g>
          {links.map((l) => {
            const s = l.source;
            const t = l.target;
            if (typeof s === "string" || typeof t === "string") return null;
            if (s.x == null || s.y == null || t.x == null || t.y == null) return null;
            const dimmed = linkDimmed(l);
            return (
              <line
                key={l.key}
                x1={s.x}
                y1={s.y}
                x2={t.x}
                y2={t.y}
                stroke={LINE_COLOR[l.type]}
                strokeWidth={STROKE_WIDTH[l.type]}
                strokeDasharray={l.type === "NV12" ? "8 6" : undefined}
                opacity={dimmed ? 0.18 : 0.95}
                style={
                  l.type === "NV12"
                    ? { animation: "scan-flow 2.4s linear infinite" }
                    : undefined
                }
                className="cursor-pointer"
                onMouseEnter={() => {
                  setHoverEdge({
                    source: s.id,
                    target: t.id,
                    type: l.type,
                    x: (s.x! + t.x!) / 2,
                    y: (s.y! + t.y!) / 2,
                  });
                }}
                onMouseLeave={() => setHoverEdge(null)}
              />
            );
          })}
        </g>

        {/* nodes */}
        <g>
          {nodes.map((n) => {
            const subtitle =
              n.kind === "nic"
                ? "ConnectX-6"
                : typeof n.numa === "number"
                  ? `numa ${n.numa}`
                  : "";
            return (
              <g
                key={n.id}
                transform={`translate(${n.x ?? 0}, ${n.y ?? 0})`}
                className="cursor-pointer"
                opacity={isolated(n.id) ? 0.25 : 1}
                onClick={(e) => {
                  e.stopPropagation();
                  setSelected(selected === n.id ? null : n.id);
                }}
              >
                <circle r={42} fill="url(#gpu-ring)" />
                <circle
                  r={n.kind === "gpu" ? 26 : 22}
                  fill="var(--color-bg-panel)"
                  stroke={
                    n.kind === "gpu" ? "var(--color-accent)" : "var(--color-warn)"
                  }
                  strokeWidth={selected === n.id ? 2 : 1.25}
                />
                <text
                  y={-2}
                  textAnchor="middle"
                  className="font-mono fill-[var(--color-fg)]"
                  fontSize={11}
                  fontWeight={600}
                >
                  {n.id}
                </text>
                <text
                  y={11}
                  textAnchor="middle"
                  className="font-mono fill-[var(--color-fg-mute)]"
                  fontSize={8}
                >
                  {subtitle}
                </text>
                {n.pciSlot && (
                  <text
                    y={n.kind === "gpu" ? 36 : 33}
                    textAnchor="middle"
                    className="font-mono fill-[var(--color-fg-mute)]"
                    fontSize={8}
                  >
                    {n.pciSlot}
                  </text>
                )}
              </g>
            );
          })}
        </g>
      </svg>

      <AnimatePresence>
        {hoverEdge && (
          <motion.div
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 4 }}
            transition={{ duration: 0.12 }}
            className="absolute pointer-events-none px-2.5 py-1.5 panel border-[var(--color-border-hi)] bg-[var(--color-bg-deep)] font-mono text-[10px] leading-snug min-w-max"
            style={{
              left: `${(hoverEdge.x / width) * 100}%`,
              top: `${(hoverEdge.y / height) * 100}%`,
              transform: "translate(-50%, -120%)",
            }}
          >
            <div className="text-[var(--color-accent)]">
              {hoverEdge.source} ↔ {hoverEdge.target}{" "}
              <span className="text-[var(--color-fg-mute)]">[{hoverEdge.type}]</span>
            </div>
            <div className="text-[var(--color-fg-dim)] max-w-xs whitespace-normal">
              {describeLink(hoverEdge.type, nvlinkPerGpu, nvlinkSpeedGBs)}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="mt-3 flex items-center gap-4 font-mono text-[10px] text-[var(--color-fg-mute)]">
        <Legend label="NV12" color={TOPO_COLOR.NV12} hint="12 NVLinks" />
        <Legend label="NODE" color={TOPO_COLOR.NODE} hint="same host bridge" />
        <Legend label="SYS" color={TOPO_COLOR.SYS} hint="cross-socket" />
        <span
          className={cn(
            "ml-auto cursor-pointer transition-colors",
            selected ? "text-[var(--color-accent)]" : "text-[var(--color-fg-mute)]",
          )}
          onClick={() => setSelected(null)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setSelected(null);
          }}
          tabIndex={0}
          role="button"
        >
          {selected ? `isolating ${selected} · click empty space to reset` : "click any node to isolate"}
        </span>
      </div>
    </div>
  );
}

function Legend({ label, color, hint }: { label: string; color: string; hint: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="w-3 h-0.5" style={{ background: color }} />
      <span className="text-[var(--color-fg-dim)]">{label}</span>
      <span className="text-[var(--color-fg-mute)]">{hint}</span>
    </span>
  );
}
