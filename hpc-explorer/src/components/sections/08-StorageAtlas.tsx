import { useMemo, useState } from "react";
import type { ClusterSnapshot } from "@/lib/types";
import { Section } from "@/components/primitives/Section";
import { Panel } from "@/components/primitives/Panel";
import { Bar } from "@/components/primitives/Bar";
import { Badge } from "@/components/primitives/Badge";
import { KV } from "@/components/primitives/KV";
import { cn } from "@/lib/cn";
import { groupBy } from "@/lib/format";
import { FILTER_INPUT_CLS } from "@/lib/style";

const HIGHLIGHT_PATHS = ["home", "debette", "aramis"];

export function StorageAtlasSection({ data }: { data: ClusterSnapshot }) {
  const [filter, setFilter] = useState("");
  const [openServers, setOpenServers] = useState<Set<string>>(() => new Set());

  const groupedNfs = useMemo(() => {
    const lf = filter.toLowerCase();
    const groups = groupBy(data.nfsMounts, (m) => m.server || "(unknown)");
    return Array.from(groups.entries())
      .map(([server, mounts]) => ({
        server,
        mounts: lf
          ? mounts.filter((m) => m.mount.toLowerCase().includes(lf))
          : mounts,
      }))
      .filter((g) => g.mounts.length > 0)
      .sort((a, b) => b.mounts.length - a.mounts.length);
  }, [data.nfsMounts, filter]);

  const toggleServer = (s: string) => {
    setOpenServers((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });
  };

  return (
    <Section
      index={8}
      id="storage"
      title="Storage atlas"
      subtitle={`${data.xfsMounts.length} XFS local · ${data.userPaths.length} user-relevant · ${data.nfsMounts.length} NFS mounts on ${groupedNfs.length} servers`}
    >
      <div className="space-y-4">
        <Panel title="user-relevant paths" subtitle="$HOME, lab share, scratch, tmpfs" glow="accent">
          <div className="space-y-2">
            {data.userPaths.map((p) => {
              const highlight = HIGHLIGHT_PATHS.some((h) => p.path.includes(h));
              return (
                <div
                  key={p.path}
                  className={cn(
                    "grid grid-cols-[1fr_8rem_2.5rem_2.5rem_4.5rem] items-center gap-3 px-2 py-1.5 border",
                    highlight
                      ? "border-[var(--color-accent)]/40 bg-[var(--color-accent-deep)]/15"
                      : "border-[var(--color-border-lo)]",
                  )}
                >
                  <div className="font-mono text-[11px] truncate" title={p.path}>
                    <span className={highlight ? "text-[var(--color-accent)]" : "text-[var(--color-fg)]"}>
                      {p.path}
                    </span>
                  </div>
                  <Bar value={p.usePct} variant="auto" size="xs" animate={false} />
                  <span className="font-mono text-[10px] tabular text-[var(--color-fg-dim)] text-right">
                    {p.usePct}%
                  </span>
                  <span className="font-mono text-[10px] tabular text-[var(--color-fg-mute)] text-right">
                    {p.free}
                  </span>
                  <div className="flex items-center justify-end gap-1">
                    <Badge variant={p.rw === "rw" ? "good" : "dim"} size="xs">
                      {p.rw}
                    </Badge>
                    <Badge variant="outline" size="xs">{p.fs}</Badge>
                  </div>
                </div>
              );
            })}
          </div>
        </Panel>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Panel title="local XFS mounts" subtitle="block · inode · sector · AGs · log">
            <table className="w-full font-mono text-[11px]">
              <thead>
                <tr className="text-[var(--color-fg-mute)] uppercase tracking-eyebrow text-[9px] border-b border-[var(--color-border-lo)]">
                  <th className="text-left py-1">mount</th>
                  <th className="text-right py-1">block</th>
                  <th className="text-right py-1">inode</th>
                  <th className="text-right py-1">AGs</th>
                  <th className="text-right py-1">log</th>
                </tr>
              </thead>
              <tbody>
                {data.xfsMounts.map((m) => (
                  <tr key={m.mount} className="border-b border-[var(--color-border-lo)]/50">
                    <td className="py-1.5 text-[var(--color-fg)]">{m.mount}</td>
                    <td className="py-1.5 tabular text-[var(--color-fg-dim)] text-right">{m.block}</td>
                    <td className="py-1.5 tabular text-[var(--color-fg-dim)] text-right">{m.inode}</td>
                    <td className="py-1.5 tabular text-[var(--color-fg-dim)] text-right">{m.ags}</td>
                    <td className="py-1.5 tabular text-[var(--color-fg-dim)] text-right">{m.logBlocks}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="mt-3 pt-2 border-t border-[var(--color-border-lo)]">
              <KV
                k="block device"
                v={data.blockDevices[0]?.model.trim() ?? "—"}
                hint={data.blockDevices[0]?.size}
              />
              <KV k="bus" v={data.blockDevices[0]?.bus || "—"} />
              <KV k="kind" v={data.blockDevices[0]?.kind || "—"} />
            </div>
          </Panel>

          <Panel
            title="storage env"
            subtitle="environment variables seeded by SLURM/system"
          >
            <div className="space-y-1.5">
              {data.storageEnvVars.map((v) => (
                <KV key={v.variable} k={v.variable} v={v.value} truncate />
              ))}
            </div>
          </Panel>
        </div>

        <Panel
          title="NFS landscape"
          subtitle={`${data.nfsMounts.length} NFS mounts across ${groupedNfs.length} servers`}
          rightSlot={
            <input
              type="text"
              placeholder="filter mount path…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className={FILTER_INPUT_CLS}
            />
          }
        >
          <div className="space-y-1.5">
            {groupedNfs.length === 0 && filter.length > 0 && (
              <div className="px-2.5 py-4 text-center font-mono text-[11px] text-[var(--color-fg-mute)]">
                no NFS mounts match “{filter}”
              </div>
            )}
            {groupedNfs.map((g) => {
              const isOpen = openServers.has(g.server) || filter.length > 0;
              const panelId = `nfs-server-${g.server.replace(/[^a-z0-9]/gi, "-")}`;
              return (
                <div key={g.server} className="border border-[var(--color-border-lo)]">
                  <button
                    type="button"
                    aria-expanded={isOpen}
                    aria-controls={panelId}
                    onClick={() => toggleServer(g.server)}
                    className="w-full px-2.5 py-1.5 flex items-center justify-between hover:bg-[var(--color-bg-elev)]/40 transition-colors font-mono"
                  >
                    <span className="flex items-center gap-2 text-[11px]">
                      <span aria-hidden="true" className="text-[var(--color-accent)]">
                        {isOpen ? "▾" : "▸"}
                      </span>
                      <span className="text-[var(--color-fg)] truncate">{g.server}</span>
                    </span>
                    <span className="text-[10px] text-[var(--color-fg-mute)] tabular">
                      {g.mounts.length} mounts
                    </span>
                  </button>
                  {isOpen && (
                    <div
                      id={panelId}
                      className="px-2.5 pb-2 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-3 gap-y-0.5"
                    >
                      {g.mounts.map((m) => {
                        const isHighlight = HIGHLIGHT_PATHS.some((h) => m.mount.includes(h));
                        return (
                          <div
                            key={m.mount}
                            className="flex items-baseline justify-between gap-2 font-mono text-[10px] py-0.5"
                          >
                            <span
                              className={cn(
                                "truncate tabular",
                                isHighlight ? "text-[var(--color-accent)]" : "text-[var(--color-fg-dim)]",
                              )}
                              title={m.mount}
                            >
                              {m.mount}
                            </span>
                            <span className="text-[var(--color-fg-mute)] shrink-0 tabular text-[9px]">
                              v{m.vers} · {m.rsize}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </Panel>
      </div>
    </Section>
  );
}
