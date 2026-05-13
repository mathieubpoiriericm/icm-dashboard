import { MotionConfig } from "motion/react";
import { TooltipProvider } from "@/components/primitives/Tooltip";
import { ChordNavPill } from "@/components/ChordNavPill";
import { TelemetryBar } from "@/components/telemetry/TelemetryBar";
import { useSectionNav } from "@/hooks/useSectionNav";
import { IdentitySection } from "@/components/sections/01-Identity";
import { NvLinkTopologySection } from "@/components/sections/02-NvLinkTopology";
import { CpuNumaSection } from "@/components/sections/03-CpuNuma";
import { CudaStackSection } from "@/components/sections/04-CudaStack";
import { ModulesSection } from "@/components/sections/05-Modules";
import { PartitionsSection } from "@/components/sections/06-Partitions";
import { QosFairshareSection } from "@/components/sections/07-QosFairshare";
import { StorageAtlasSection } from "@/components/sections/08-StorageAtlas";
import { clusterSnapshot } from "@/data/probe";

export function App() {
  const data = clusterSnapshot;
  const { lastTarget } = useSectionNav();
  return (
    <MotionConfig reducedMotion="user">
      <TooltipProvider delayDuration={80}>
        <div className="min-h-dvh">
          <a
            href="#main-content"
            className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:px-3 focus:py-1.5 focus:bg-[var(--color-accent)] focus:text-black focus:font-mono focus:text-[11px]"
          >
            Skip to content
          </a>
          <h1 className="sr-only">
            HPC Explorer — {data.meta.host} · {data.meta.cluster}
          </h1>
          <ChordNavPill target={lastTarget} />
          <TelemetryBar data={data} />
          <main id="main-content" className="max-w-[1440px] mx-auto pb-32">
            <IdentitySection data={data} />
            <NvLinkTopologySection data={data} />
            <CpuNumaSection data={data} />
            <CudaStackSection data={data} />
            <ModulesSection data={data} />
            <PartitionsSection data={data} />
            <QosFairshareSection data={data} />
            <StorageAtlasSection data={data} />
          </main>
          <footer className="px-6 py-8 border-t border-[var(--color-border)] bg-[var(--color-bg-deep)]/60">
            <div className="max-w-[1440px] mx-auto flex flex-wrap items-center justify-between gap-4 font-mono text-[10px] text-[var(--color-fg-mute)]">
              <span>
                probed {data.meta.probedAt} · {data.meta.cluster} / {data.meta.host} · job{" "}
                {data.meta.jobId}
              </span>
              <span className="flex items-center gap-3">
                <kbd className="px-1.5 py-0.5 border border-[var(--color-border-hi)] rounded-sm tabular text-[var(--color-fg-dim)]">
                  g 1–8
                </kbd>
                <span>jump to section</span>
                <span className="text-[var(--color-fg-mute)]">·</span>
                <kbd className="px-1.5 py-0.5 border border-[var(--color-border-hi)] rounded-sm tabular text-[var(--color-fg-dim)]">
                  g h
                </kbd>
                <span>top</span>
              </span>
            </div>
          </footer>
        </div>
      </TooltipProvider>
    </MotionConfig>
  );
}
