import type { ClusterSnapshot } from "@/lib/types";
import { Section } from "@/components/primitives/Section";
import { Panel } from "@/components/primitives/Panel";
import { KV } from "@/components/primitives/KV";
import { Bar } from "@/components/primitives/Bar";
import { Badge } from "@/components/primitives/Badge";
import { NumaSocketDiagram } from "@/viz/NumaSocketDiagram";
import { formatGB } from "@/lib/format";

export function CpuNumaSection({ data }: { data: ClusterSnapshot }) {
  const memUsedPct =
    data.memory.totalGB > 0
      ? ((data.memory.totalGB - data.memory.freeGB) / data.memory.totalGB) * 100
      : 0;

  return (
    <Section
      index={3}
      id="cpu"
      title="CPU & NUMA architecture"
      subtitle={`${data.cpu.model} · ${data.cpu.sockets} sockets × ${data.cpu.coresPerSocket} cores · ${data.cpu.numaNodes} NUMA nodes`}
      rightSlot={
        <Badge variant="accent">{data.cpu.logicalCpus} threads</Badge>
      }
    >
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="xl:col-span-2">
          <NumaSocketDiagram cpu={data.cpu} topology={data.gpuTopology} />
        </div>

        <div className="space-y-4">
          <Panel title="memory" corners>
            <div className="space-y-2.5">
              <div>
                <div className="flex items-baseline justify-between mb-1 font-mono text-[10px]">
                  <span className="text-[var(--color-fg-mute)] uppercase tracking-eyebrow">
                    DDR4
                  </span>
                  <span className="tabular text-[var(--color-fg-dim)]">
                    {formatGB(data.memory.totalGB - data.memory.freeGB)} / {formatGB(data.memory.totalGB)}
                  </span>
                </div>
                <Bar value={memUsedPct} variant="auto" size="md" animate />
              </div>
              <div className="space-y-1.5 pt-2 border-t border-[var(--color-border-lo)]">
                <KV k="total" v={formatGB(data.memory.totalGB)} />
                <KV k="available" v={formatGB(data.memory.availableGB)} />
                <KV k="free" v={formatGB(data.memory.freeGB)} variant="good" />
                <KV k="swap" v={data.memory.swap || "0B"} variant="warn" />
                <KV
                  k="/dev/shm"
                  v={formatGB(data.memory.shmGB)}
                  hint="tmpfs"
                  variant="accent"
                />
              </div>
            </div>
          </Panel>

          <Panel title="PCIe device map" subtitle="lspci excerpt — GPUs, NICs, storage">
            <ul className="space-y-1.5 font-mono">
              {data.pciHCAs.map((d) => (
                <li
                  key={d.slot}
                  className="grid grid-cols-[3rem_1fr] items-baseline gap-2"
                >
                  <span className="text-[10px] text-[var(--color-accent)] tabular">
                    {d.slot}
                  </span>
                  <div className="min-w-0">
                    <div
                      className="text-[11px] text-[var(--color-fg)] truncate"
                      title={d.vendorDevice}
                    >
                      {d.vendorDevice}
                    </div>
                    <div className="text-[9px] text-[var(--color-fg-mute)] uppercase tracking-eyebrow">
                      {d.class}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
            <div className="mt-3 pt-2 border-t border-[var(--color-border-lo)] text-[10px] text-[var(--color-fg-mute)] font-mono">
              local disk: {data.blockDevices[0]?.model.trim()} ·{" "}
              {data.blockDevices[0]?.size}
            </div>
          </Panel>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Panel title="network interfaces" subtitle="ip -br addr">
          <div className="space-y-1">
            {data.network.map((n) => (
              <div key={n.iface} className="flex items-center gap-2 font-mono text-[11px]">
                <Badge variant={n.state === "UP" ? "good" : "dim"} size="xs">
                  {n.state}
                </Badge>
                <span className="text-[var(--color-fg)] tabular w-20 shrink-0">
                  {n.iface}
                </span>
                <span className="text-[var(--color-fg-dim)] tabular truncate">
                  {n.addr || "—"}
                </span>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="cgroup affinity" glow="warn">
          <div className="space-y-2 font-mono text-xs">
            <div>
              <div className="text-[10px] text-[var(--color-fg-mute)] uppercase tracking-eyebrow mb-1">
                cpu affinity mask
              </div>
              <div className="text-[var(--color-warn)] tabular text-2xl leading-none">
                {data.jobLimits.cpuAffinityMask}
              </div>
            </div>
            <div className="text-[var(--color-fg-dim)] text-[10px] leading-relaxed">
              Even with {data.cpu.logicalCpus} logical CPUs visible, this job's
              cgroup pins it to a single core. The full thread budget is
              available only when the sbatch requests it explicitly.
            </div>
            <div className="pt-2 border-t border-[var(--color-border-lo)] text-[10px] text-[var(--color-fg-mute)] break-all">
              {data.jobLimits.cgroupMembership}
            </div>
          </div>
        </Panel>

        <Panel title="process limits">
          <div className="space-y-1.5">
            <KV k="open files (-n)" v={data.procLimits.openFiles} />
            <KV k="file size (-f)" v={data.procLimits.fileSize} />
            <KV k="locked mem (-l)" v={data.procLimits.lockedMemoryKB} hint="KB" />
          </div>
        </Panel>
      </div>
    </Section>
  );
}
