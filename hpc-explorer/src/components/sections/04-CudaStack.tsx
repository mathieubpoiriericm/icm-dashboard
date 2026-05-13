import { useMemo, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import type { ClusterSnapshot } from "@/lib/types";
import { Section } from "@/components/primitives/Section";
import { Panel } from "@/components/primitives/Panel";
import { Badge } from "@/components/primitives/Badge";
import { CudaStackTower } from "@/viz/CudaStackTower";
import { cn } from "@/lib/cn";
import { EASE_SNAP } from "@/lib/motion";

export function CudaStackSection({ data }: { data: ClusterSnapshot }) {
  const [expanded, setExpanded] = useState(false);
  const missing = useMemo(
    () => data.vendoredCuda.libs.filter((l) => !l.inLdPath),
    [data.vendoredCuda.libs],
  );

  return (
    <Section
      index={4}
      id="cuda-stack"
      title="CUDA + PyTorch stack"
      subtitle="Six layers from silicon to fine-tuning. The amber ribbon is real: vendored CUDA shared objects sit beside torch but aren't on LD_LIBRARY_PATH."
      rightSlot={
        <Badge variant={data.vendoredCuda.warning ? "warn" : "good"} pulse={data.vendoredCuda.warning}>
          {data.vendoredCuda.warning ? `${missing.length} sublibs unlinked` : "linker OK"}
        </Badge>
      }
    >
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="xl:col-span-2">
          <CudaStackTower data={data} />
        </div>

        <div className="space-y-4">
          <Panel
            title="vendored CUDA loader path"
            subtitle="none of these are in LD_LIBRARY_PATH"
            glow={data.vendoredCuda.warning ? "warn" : "none"}
          >
            <div className="font-mono text-[10px] text-[var(--color-fg-mute)] mb-2 break-all">
              <span className="text-[var(--color-fg-dim)]">root:</span>{" "}
              {data.vendoredCuda.root || "(not reported)"}
            </div>

            <button
              type="button"
              aria-expanded={expanded}
              aria-controls="vendored-cuda-libs"
              onClick={() => setExpanded((e) => !e)}
              className={cn(
                "w-full text-left flex items-center justify-between",
                "px-2 py-1.5 border border-[var(--color-warn)]/40",
                "bg-[var(--color-warn-lo)]/20 hover:bg-[var(--color-warn-lo)]/30",
                "transition-colors font-mono text-[11px]",
              )}
            >
              <span className="text-[var(--color-warn)]">
                {data.vendoredCuda.libs.length} sublibs · {missing.length} unlinked
              </span>
              <span aria-hidden="true" className="text-[var(--color-fg-mute)]">
                {expanded ? "−" : "+"}
              </span>
            </button>

            <AnimatePresence initial={false}>
              {expanded && (
                <motion.ul
                  id="vendored-cuda-libs"
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.25, ease: EASE_SNAP }}
                  className="mt-2 overflow-hidden font-mono text-[10px] space-y-0.5"
                >
                  {data.vendoredCuda.libs.map((l) => (
                    <li
                      key={l.sublib}
                      className="flex items-center justify-between border-b border-[var(--color-border-lo)] py-1"
                    >
                      <span className="flex items-center gap-2">
                        <span
                          className={cn(
                            "w-1.5 h-1.5 rounded-full",
                            l.inLdPath
                              ? "bg-[var(--color-good)]"
                              : "bg-[var(--color-warn)]",
                          )}
                        />
                        <span className="text-[var(--color-fg)]">{l.sublib}</span>
                      </span>
                      <span className="text-[var(--color-fg-mute)] tabular">
                        {l.nSos}{" "}
                        <span className="text-[8px] uppercase tracking-eyebrow">.so</span>
                      </span>
                    </li>
                  ))}
                </motion.ul>
              )}
            </AnimatePresence>

            <p className="mt-3 text-[10px] text-[var(--color-fg-dim)] leading-relaxed">
              <span className="text-[var(--color-warn)]">⚠</span> torch may fail to
              dlopen at first cuda call. Common fix: prepend{" "}
              <code className="text-[var(--color-accent)] tabular">
                $VIRTUAL_ENV/lib/python3.12/site-packages/nvidia/*/lib
              </code>{" "}
              to <code className="text-[var(--color-accent)]">LD_LIBRARY_PATH</code>{" "}
              in the sbatch.
            </p>
          </Panel>

          <Panel title="huggingface cache">
            <div className="font-mono text-[10px] break-all">
              <div className="text-[var(--color-fg-mute)] uppercase tracking-eyebrow mb-1">
                HF_HOME
              </div>
              <div className="text-[var(--color-fg-dim)]">{data.hfCache.hfHome}</div>
            </div>
          </Panel>

          <Panel title="python env" subtitle="venv attached to project dir">
            <ul className="space-y-1 font-mono text-[10px]">
              <li>
                <span className="text-[var(--color-fg-mute)]">venv: </span>
                <span
                  className={
                    data.pythonEnv.venvActive
                      ? "text-[var(--color-good)]"
                      : "text-[var(--color-warn)]"
                  }
                >
                  {data.pythonEnv.venvActive ? "active" : "inactive"}
                </span>
              </li>
              <li className="text-[var(--color-fg-dim)] break-all">
                <span className="text-[var(--color-fg-mute)]">python: </span>
                {data.pythonEnv.interpreter}
              </li>
              <li className="text-[var(--color-fg-dim)] break-all">
                <span className="text-[var(--color-fg-mute)]">project: </span>
                {data.pythonEnv.projectDir}
              </li>
              <li className="text-[var(--color-fg-dim)] break-all">
                <span className="text-[var(--color-fg-mute)]">lock: </span>
                {data.pythonEnv.uvLock}
              </li>
            </ul>
          </Panel>
        </div>
      </div>
    </Section>
  );
}
