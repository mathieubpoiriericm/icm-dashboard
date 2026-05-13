// =============================================================================
// TOPOLOGY HELPERS
// Shared maps and accessors for the GPU/NIC fabric. The NvLink force graph,
// matrix, mini-matrix, NUMA strip, and NUMA socket diagram all read from here.
// =============================================================================

import type { GpuTopology, PciHca, TopoLink } from "./types";

// X is intentionally omitted: matrix cells render it as bg-deep, the force
// graph renders it as transparent (skipped lines). Each caller supplies its
// own fallback for X.
export const TOPO_COLOR: Record<Exclude<TopoLink, "X">, string> = {
  NV12: "var(--color-accent)",
  NODE: "var(--color-warn)",
  SYS: "color-mix(in oklab, var(--color-fg-mute) 35%, transparent)",
  PHB: "var(--color-fg-mute)",
  PXB: "var(--color-fg-dim)",
  PIX: "var(--color-good)",
};

export const TOPO_LABEL: Record<TopoLink, string> = {
  X: "Self",
  NV12: "12 NVLinks · 300 GB/s per direction",
  NODE: "Same NUMA host bridge — PCIe via shared bridge",
  SYS: "Cross-socket — PCIe + UPI inter-socket interconnect",
  PHB: "PCIe host bridge (typically the CPU)",
  PXB: "Multiple PCIe bridges, no host bridge",
  PIX: "Single PCIe bridge",
};

export const TOPO_LINKS: ReadonlySet<TopoLink> = new Set([
  "X",
  "NV12",
  "SYS",
  "NODE",
  "PHB",
  "PXB",
  "PIX",
]);

export function shortNodeId(id: string): string {
  return id.replace("GPU", "G").replace("NIC", "N");
}

// Map of "GPU0".."GPUn" / "NIC0" → PCI slot. Callers that need a numeric-keyed
// map can derive it (slots[`GPU${idx}`]).
export function gpuPciSlots(pciHCAs: PciHca[]): Record<string, string> {
  const out: Record<string, string> = {};
  let g = 0;
  for (const hca of pciHCAs) {
    if (hca.class === "3D controller") {
      out[`GPU${g}`] = hca.slot;
      g++;
    } else if (hca.vendorDevice.toLowerCase().includes("mellanox")) {
      out["NIC0"] = hca.slot;
    }
  }
  return out;
}

// Group GPUs/NICs by their NUMA domain. NIC0 is also placed on the NUMA of any
// node it has a NODE link to, since the NIC inherits the host bridge.
export function numaOccupants(topology: GpuTopology): Record<number, string[]> {
  const occupants: Record<number, string[]> = {};
  for (const [node, numa] of Object.entries(topology.numaAffinity)) {
    if (typeof numa === "number") {
      occupants[numa] ??= [];
      occupants[numa].push(node);
    }
  }
  const nicTopo = topology.matrix["NIC0"];
  if (nicTopo) {
    for (const [target, link] of Object.entries(nicTopo)) {
      if (link !== "NODE") continue;
      const targetNuma = topology.numaAffinity[target];
      if (typeof targetNuma !== "number") continue;
      occupants[targetNuma] ??= [];
      if (!occupants[targetNuma].includes("NIC0")) {
        occupants[targetNuma].push("NIC0");
      }
    }
  }
  return occupants;
}
