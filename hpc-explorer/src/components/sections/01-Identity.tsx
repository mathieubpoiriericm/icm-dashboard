import type { ClusterSnapshot } from "@/lib/types";
import { Section } from "@/components/primitives/Section";
import { Panel } from "@/components/primitives/Panel";
import { KV } from "@/components/primitives/KV";
import { ScrambleText } from "@/components/primitives/ScrambleText";
import { Badge } from "@/components/primitives/Badge";

interface IdentitySectionProps {
  data: ClusterSnapshot;
}

export function IdentitySection({ data }: IdentitySectionProps) {
  return (
    <Section
      index={1}
      id="identity"
      title="Identity & job context"
      subtitle="Who, what, where, when — anchored to the SLURM allocation that produced this probe."
    >
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Panel
          title="compute host"
          subtitle={data.computeHost.hostname}
          className="font-mono"
          glow="accent"
        >
          <div className="space-y-3">
            <div className="font-mono">
              <div className="text-[var(--color-fg-mute)] text-[10px] uppercase tracking-eyebrow">
                hostname
              </div>
              <div className="text-2xl text-[var(--color-fg)] tracking-tight">
                <ScrambleText text={data.meta.host} duration={1.2} delay={0.2} />
              </div>
            </div>
            <div className="space-y-1.5 pt-3 border-t border-[var(--color-border-lo)]">
              <KV k="cluster" v={data.meta.cluster} variant="accent" />
              <KV k="OS" v={data.meta.os} />
              <KV k="kernel" v={data.meta.kernel} />
              <KV k="uptime" v={data.computeHost.uptime} />
              <KV k="logical CPUs" v={data.computeHost.logicalCpus} />
            </div>
          </div>
        </Panel>

        <Panel
          title="slurm allocation"
          subtitle={`Job ${data.slurmJobContext.jobId} · ${data.meta.slurmVersion}`}
          rightSlot={
            <Badge variant="good" pulse>
              {data.slurmNodeView.state}
            </Badge>
          }
        >
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <KV k="account" v={data.meta.account} />
              <KV k="partition" v={data.slurmJobContext.partition} variant="accent" />
              <KV k="qos" v={data.slurmJobContext.qos} />
              <KV k="user" v={`${data.meta.user.name}`} hint={`uid ${data.meta.user.uid}`} />
            </div>
            <div className="space-y-1.5">
              <KV k="GPUs" v={data.slurmJobContext.gpusOnNode} variant="accent" />
              <KV k="CPUs" v={data.slurmJobContext.cpusOnNode} />
              <KV k="mem alloc" v={data.slurmJobContext.memPerNode} />
              <KV
                k="visible"
                v={data.slurmJobContext.cudaVisible}
                hint="$CUDA_VISIBLE_DEVICES"
              />
            </div>
          </div>
          <div className="mt-4 pt-3 border-t border-[var(--color-border-lo)]">
            <div className="font-mono text-[10px] uppercase tracking-eyebrow text-[var(--color-fg-mute)] mb-1.5">
              cgroup constraint
            </div>
            <div className="font-mono text-xs text-[var(--color-fg-dim)] break-all">
              <span className="text-[var(--color-warn)]">cpu_affinity_mask</span> ={" "}
              <span className="text-[var(--color-fg)] tabular">
                {data.jobLimits.cpuAffinityMask}
              </span>
              <span className="text-[var(--color-fg-mute)] ml-2">
                (single-core lock — node has {data.cpu.logicalCpus})
              </span>
            </div>
            <div className="font-mono text-xs text-[var(--color-fg-mute)] mt-1 break-all">
              {data.jobLimits.cgroupMembership}
            </div>
          </div>
        </Panel>
      </div>
    </Section>
  );
}
