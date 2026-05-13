import { useMemo, useState } from "react";
import type { ClusterSnapshot, Partition } from "@/lib/types";
import { Section } from "@/components/primitives/Section";
import { Panel } from "@/components/primitives/Panel";
import { Bar } from "@/components/primitives/Bar";
import { Badge } from "@/components/primitives/Badge";
import { cn } from "@/lib/cn";
import { FILTER_INPUT_CLS } from "@/lib/style";
import { groupBy, parseMemoryGB } from "@/lib/format";

type SortKey = "name" | "cpuUse" | "memPerNode" | "gres" | "totalNodes";
type SortDir = "asc" | "desc";

export function PartitionsSection({ data }: { data: ClusterSnapshot }) {
  const [filter, setFilter] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("cpuUse");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const rows = useMemo(() => {
    const lf = filter.toLowerCase();
    const filtered = lf
      ? data.partitions.filter(
          (p) =>
            p.name.toLowerCase().includes(lf) ||
            p.gres.toLowerCase().includes(lf),
        )
      : data.partitions;
    const sign = sortDir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => sign * compare(a, b, sortKey));
  }, [data.partitions, filter, sortKey, sortDir]);

  const gpuByPartition = useMemo(
    () => groupBy(data.gpuResources, (r) => r.partition),
    [data.gpuResources],
  );

  const toggleSort = (k: SortKey) => {
    if (k === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(k);
      setSortDir(k === "name" ? "asc" : "desc");
    }
  };

  return (
    <Section
      index={6}
      id="partitions"
      title="Partition explorer"
      subtitle={`${data.partitions.length} partitions on ${data.schedulerConfig.clusterName} · ${data.schedulerConfig.schedulerType}`}
      rightSlot={
        <input
          type="text"
          placeholder="filter…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className={FILTER_INPUT_CLS}
        />
      }
    >
      <Panel corners className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full font-mono text-[11px]">
            <thead>
              <tr className="text-[var(--color-fg-mute)] uppercase tracking-eyebrow text-[10px] border-b border-[var(--color-border)]">
                <Th label="partition" sortKey="name" current={sortKey} dir={sortDir} onClick={toggleSort} />
                <Th label="state" sortKey={null} />
                <Th label="nodes A/I/O/T" sortKey="totalNodes" current={sortKey} dir={sortDir} onClick={toggleSort} />
                <Th label="cpu utilization" sortKey="cpuUse" current={sortKey} dir={sortDir} onClick={toggleSort} />
                <Th label="mem/node" sortKey="memPerNode" current={sortKey} dir={sortDir} onClick={toggleSort} />
                <Th label="gres" sortKey="gres" current={sortKey} dir={sortDir} onClick={toggleSort} />
                <Th label="time" sortKey={null} />
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td
                    colSpan={7}
                    className="px-3 py-6 text-center text-[var(--color-fg-mute)] tabular"
                  >
                    no partitions match “{filter}”
                  </td>
                </tr>
              ) : (
                rows.map((p) => (
                  <Row
                    key={p.name}
                    p={p}
                    active={p.name === data.slurmJobContext.partition}
                    gpus={gpuByPartition.get(p.name) ?? []}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="mt-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Panel title="drained / down nodes" glow={data.drainedNodes.length > 0 ? "warn" : "none"}>
          {data.drainedNodes.length === 0 ? (
            <div className="font-mono text-[11px] text-[var(--color-fg-mute)]">
              all nodes healthy
            </div>
          ) : (
            <ul className="space-y-1.5 font-mono text-[11px]">
              {data.drainedNodes.map((n) => (
                <li key={n.node} className="flex items-baseline justify-between gap-2">
                  <span className="flex items-center gap-2">
                    <Badge variant="warn" size="xs">{n.state}</Badge>
                    <span className="text-[var(--color-fg)] tabular">{n.node}</span>
                  </span>
                  <span className="text-[var(--color-fg-mute)] truncate" title={`${n.reason} · since ${n.since}`}>
                    {n.reason} · {n.since.slice(0, 10)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="pending job reasons">
          {data.pendingReasons.length === 0 ? (
            <div className="font-mono text-[11px] text-[var(--color-fg-mute)]">
              no pending jobs
            </div>
          ) : (
            <div className="space-y-2">
              {data.pendingReasons.map((r) => {
                const pct = (r.jobs / Math.max(1, data.clusterQueue.pending)) * 100;
                return (
                  <div key={r.reason}>
                    <div className="flex items-baseline justify-between mb-1 font-mono text-[11px]">
                      <span className="text-[var(--color-fg-dim)]">{r.reason}</span>
                      <span className="tabular text-[var(--color-warn)]">{r.jobs}</span>
                    </div>
                    <Bar value={pct} variant="warn" size="xs" animate />
                  </div>
                );
              })}
            </div>
          )}
        </Panel>
      </div>
    </Section>
  );
}

function compare(a: Partition, b: Partition, key: SortKey): number {
  switch (key) {
    case "name":
      return a.name.localeCompare(b.name);
    case "cpuUse":
      return a.cpuUsePct - b.cpuUsePct;
    case "memPerNode":
      return parseMemoryGB(a.memPerNode) - parseMemoryGB(b.memPerNode);
    case "gres":
      return a.gres.localeCompare(b.gres);
    case "totalNodes":
      return a.nodes.total - b.nodes.total;
  }
}

function Th({
  label,
  sortKey,
  current,
  dir,
  onClick,
}: {
  label: string;
  sortKey: SortKey | null;
  current?: SortKey;
  dir?: SortDir;
  onClick?: (k: SortKey) => void;
}) {
  const active = sortKey != null && current === sortKey;
  const sortable = sortKey != null;
  const handleActivate = () => {
    if (sortable) onClick?.(sortKey);
  };
  return (
    <th
      scope="col"
      aria-sort={
        active ? (dir === "asc" ? "ascending" : "descending") : undefined
      }
      className={cn(
        "text-left px-3 py-2 select-none whitespace-nowrap",
        sortable && "cursor-pointer hover:text-[var(--color-accent)]",
        active && "text-[var(--color-accent)]",
      )}
      onClick={handleActivate}
      onKeyDown={(e) => {
        if (!sortable) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          handleActivate();
        }
      }}
      tabIndex={sortable ? 0 : undefined}
      role={sortable ? "button" : undefined}
    >
      {label}
      {active && (dir === "asc" ? " ▲" : " ▼")}
    </th>
  );
}

function Row({
  p,
  active,
  gpus,
}: {
  p: Partition;
  active: boolean;
  gpus: ClusterSnapshot["gpuResources"];
}) {
  return (
    <tr
      className={cn(
        "border-b border-[var(--color-border-lo)] transition-colors",
        "hover:bg-[var(--color-bg-elev)]/60",
        active && "bg-[var(--color-accent-deep)]/15",
      )}
    >
      <td className="px-3 py-1.5">
        <div className="flex items-center gap-2">
          {active && <span className="w-1 h-3 bg-[var(--color-accent)]" />}
          <span className={active ? "text-[var(--color-accent)]" : "text-[var(--color-fg)]"}>
            {p.name}
          </span>
        </div>
      </td>
      <td className="px-3 py-1.5">
        <Badge variant={p.avail === "up" ? "good" : "dim"} size="xs">
          {p.avail}
        </Badge>
      </td>
      <td className="px-3 py-1.5 tabular text-[var(--color-fg-dim)]">
        {p.nodes.alloc}/{p.nodes.idle}/{p.nodes.other}/{p.nodes.total}
      </td>
      <td className="px-3 py-1.5 min-w-[180px]">
        <div className="flex items-center gap-2">
          <div className="flex-1">
            <Bar value={p.cpuUsePct} variant="auto" size="xs" animate={false} />
          </div>
          <span className="tabular w-9 text-right text-[var(--color-fg-dim)]">
            {p.cpuUsePct}%
          </span>
        </div>
        <div className="text-[9px] text-[var(--color-fg-mute)] mt-0.5 tabular">
          {p.cpus.alloc}/{p.cpus.total} CPU
        </div>
      </td>
      <td className="px-3 py-1.5 tabular text-[var(--color-fg-dim)]">
        {p.memPerNode}
      </td>
      <td className="px-3 py-1.5">
        {p.gres ? (
          <div className="flex items-center gap-1.5 flex-wrap">
            <Badge variant="accent" size="xs">{p.gres}</Badge>
            {gpus.map((g) => (
              <span
                key={`${g.partition}-${g.type}`}
                className="text-[9px] tabular text-[var(--color-fg-mute)]"
                title={`${g.used}/${g.total} used · ${g.free} free`}
              >
                {g.used}/{g.total}
              </span>
            ))}
          </div>
        ) : (
          <span className="text-[var(--color-fg-mute)]">—</span>
        )}
      </td>
      <td className="px-3 py-1.5 tabular text-[var(--color-fg-mute)] text-[10px]">
        {p.timeLimit}
      </td>
    </tr>
  );
}
