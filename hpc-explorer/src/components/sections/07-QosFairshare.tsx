import type { ClusterSnapshot, QosLimit } from "@/lib/types";
import { Section } from "@/components/primitives/Section";
import { Panel } from "@/components/primitives/Panel";
import { Badge } from "@/components/primitives/Badge";
import { Bar } from "@/components/primitives/Bar";
import { KV } from "@/components/primitives/KV";
import { cn } from "@/lib/cn";
import { clamp } from "@/lib/format";

export function QosFairshareSection({ data }: { data: ClusterSnapshot }) {
  const activeQos = data.slurmJobContext.qos;

  return (
    <Section
      index={7}
      id="qos-fairshare"
      title="QOS, fairshare & jobs"
      subtitle={`${data.qosLimits.length} QOSes available to ${data.fairshare.user} on the ${data.fairshare.account} account.`}
      rightSlot={
        <Badge variant="accent">{activeQos} active</Badge>
      }
    >
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="xl:col-span-2">
          <Panel title="QOS small-multiples" subtitle="MaxTRES per user · CPU + memory ceilings">
            <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-5 gap-2">
              {data.qosLimits.map((q) => (
                <QosCard
                  key={q.qos}
                  q={q}
                  active={q.qos === activeQos}
                />
              ))}
            </div>
          </Panel>
        </div>

        <div className="space-y-4">
          <Panel title="account associations">
            {data.accountAssoc.map((a) => (
              <div key={`${a.account}-${a.partition}`} className="space-y-1.5">
                <KV k="account" v={a.account} variant="accent" />
                <KV k="partitions" v={a.partition} />
                <KV k="QOSes" v={`${a.nQos} available`} />
                <KV k="max jobs" v={a.maxJobs} />
                <KV k="max wall" v={a.maxWall || "—"} />
              </div>
            ))}
          </Panel>

          <Panel title="your jobs" glow={data.myJobs.length > 0 ? "accent" : "none"}>
            {data.myJobs.length === 0 ? (
              <div className="font-mono text-[11px] text-[var(--color-fg-mute)]">
                no jobs running
              </div>
            ) : (
              <ul className="space-y-2 font-mono text-[11px]">
                {data.myJobs.map((j) => (
                  <li
                    key={j.jobId}
                    className="border border-[var(--color-border-lo)] p-2 bg-[var(--color-bg-elev)]/40"
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[var(--color-accent)] tabular">
                        J{j.jobId}
                      </span>
                      <Badge variant="good" size="xs" pulse>
                        {j.state}
                      </Badge>
                    </div>
                    <div className="text-[var(--color-fg-dim)] mb-0.5">
                      <span className="text-[var(--color-fg-mute)]">name </span>
                      {j.name}
                    </div>
                    <div className="text-[var(--color-fg-dim)] tabular text-[10px]">
                      {j.partition} · {j.nodesOrReason} · {j.elapsed} / {j.limit}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </Section>
  );
}

function QosCard({ q, active }: { q: QosLimit; active: boolean }) {
  // Parse cpu= and mem= from MaxTresUser
  const cpuMatch = q.maxTresUser.match(/cpu=(\d+)/);
  const memMatch = q.maxTresUser.match(/mem=(\d+)([GTM]?)/i);
  const cpu = cpuMatch ? +cpuMatch[1] : 0;
  const memVal = memMatch ? +memMatch[1] : 0;
  const memUnit = memMatch ? memMatch[2].toUpperCase() : "";
  const memGB =
    memUnit === "T" ? memVal * 1024 : memUnit === "M" ? memVal / 1024 : memVal;

  const cpuPct = clamp((cpu / 300) * 100, 0, 100);
  const memPct = clamp((memGB / 5400) * 100, 0, 100);

  return (
    <div
      className={cn(
        "p-2 border font-mono",
        active
          ? "border-[var(--color-accent)]/40 bg-[var(--color-accent-deep)]/30"
          : "border-[var(--color-border)] bg-[var(--color-bg-panel)]",
        "transition-colors hover:border-[var(--color-border-hi)]",
      )}
    >
      <div className="flex items-center justify-between mb-1.5">
        <span
          className={cn(
            "text-[10px] tabular",
            active ? "text-[var(--color-accent)]" : "text-[var(--color-fg)]",
          )}
        >
          {q.qos}
        </span>
        {active && <span className="text-[8px] text-[var(--color-accent)]">●</span>}
      </div>
      <div className="space-y-1">
        <div>
          <div className="flex justify-between text-[8px] uppercase tracking-eyebrow mb-0.5">
            <span className="text-[var(--color-fg-mute)]">cpu</span>
            <span className="tabular text-[var(--color-fg-dim)]">{cpu}</span>
          </div>
          <Bar value={cpuPct} variant={active ? "accent" : "auto"} size="xs" animate={false} />
        </div>
        <div>
          <div className="flex justify-between text-[8px] uppercase tracking-eyebrow mb-0.5">
            <span className="text-[var(--color-fg-mute)]">mem</span>
            <span className="tabular text-[var(--color-fg-dim)]">
              {memVal}{memUnit}
            </span>
          </div>
          <Bar value={memPct} variant={active ? "accent" : "auto"} size="xs" animate={false} />
        </div>
      </div>
    </div>
  );
}
