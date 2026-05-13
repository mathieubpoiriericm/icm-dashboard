import { useMemo } from "react";
import type { ClusterSnapshot } from "@/lib/types";
import { Section } from "@/components/primitives/Section";
import { Panel } from "@/components/primitives/Panel";
import { Badge } from "@/components/primitives/Badge";
import { KV } from "@/components/primitives/KV";

const KNOWN_GROUPS = [
  "CUDA",
  "gcc",
  "python",
  "cudnn",
  "gcc-runtime",
  "py-biopython",
  "py-ipython",
  "py-python-dateutil",
  "py-python-json-logger",
  "python-venv",
];

const knownGroupRank = (name: string) => {
  const i = KNOWN_GROUPS.indexOf(name);
  return i === -1 ? KNOWN_GROUPS.length : i;
};

export function ModulesSection({ data }: { data: ClusterSnapshot }) {
  const grouped = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const mod of data.modules) {
      const versions = m.get(mod.name) ?? [];
      if (mod.version) versions.push(mod.version);
      m.set(mod.name, versions);
    }
    return Array.from(m.entries()).sort((a, b) => {
      const rankDiff = knownGroupRank(a[0]) - knownGroupRank(b[0]);
      return rankDiff !== 0 ? rankDiff : a[0].localeCompare(b[0]);
    });
  }, [data.modules]);

  return (
    <Section
      index={5}
      id="modules"
      title="Modules & toolchain"
      subtitle="What lmod offers vs what's actually loaded right now."
    >
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <Panel title="available modules" subtitle="filtered to cuda · gcc · python · cudnn · py-*">
            <div className="space-y-3">
              {grouped.map(([name, versions]) => (
                <div key={name}>
                  <div className="font-mono text-[10px] uppercase tracking-eyebrow text-[var(--color-fg-mute)] mb-1">
                    {name}
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {versions.length === 0 && (
                      <Badge variant="dim" size="xs">no versions listed</Badge>
                    )}
                    {versions.map((v) => (
                      <Badge
                        key={v}
                        variant={isHashedVariant(v) ? "dim" : "outline"}
                        size="sm"
                      >
                        {v}
                      </Badge>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </Panel>
        </div>

        <Panel
          title="loaded toolchain"
          subtitle="versions in the current shell"
          glow="accent"
        >
          <div className="space-y-2">
            <KV k="gcc" v={data.toolchain.gcc} variant="accent" />
            <KV k="python" v={data.toolchain.python} variant="accent" />
            <KV k="uv" v={data.toolchain.uv} />
            <KV
              k="nvidia driver"
              v={data.toolchain.nvidiaDriver}
              variant="accent"
            />
          </div>
          <div className="mt-4 pt-3 border-t border-[var(--color-border-lo)]">
            <div className="font-mono text-[10px] uppercase tracking-eyebrow text-[var(--color-fg-mute)] mb-2">
              fine-tuning libs
            </div>
            <ul className="grid grid-cols-2 gap-y-1 gap-x-3 font-mono text-[10px]">
              {data.finetuneLibs.map((l) => (
                <li
                  key={l.pkg}
                  className="flex items-baseline justify-between border-b border-[var(--color-border-lo)]/60 py-0.5"
                >
                  <span className="text-[var(--color-fg-dim)]">{l.pkg}</span>
                  <span className="text-[var(--color-fg)] tabular">{l.version}</span>
                </li>
              ))}
            </ul>
          </div>
        </Panel>
      </div>
    </Section>
  );
}

function isHashedVariant(v: string): boolean {
  return /-[a-z0-9]{7,}$/.test(v);
}
