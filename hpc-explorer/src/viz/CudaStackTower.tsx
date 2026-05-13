import { motion } from "motion/react";
import type { ClusterSnapshot } from "@/lib/types";
import { Badge } from "@/components/primitives/Badge";
import { cn } from "@/lib/cn";
import { EASE_SNAP, STAGGER } from "@/lib/motion";

interface CudaStackTowerProps {
  data: ClusterSnapshot;
}

interface Layer {
  level: number;
  label: string;
  primary: string;
  meta?: string;
  badges?: { text: string; variant?: "good" | "accent" | "warn" }[];
  tone?: "kernel" | "runtime" | "framework" | "model";
}

export function CudaStackTower({ data }: CudaStackTowerProps) {
  const layers: Layer[] = [
    {
      level: 5,
      label: "Top deck",
      primary: "Unsloth · TRL · PEFT · Transformers · BitsAndBytes",
      meta: pickVersions(data, ["unsloth", "trl", "peft", "transformers", "bitsandbytes", "xformers", "triton"]),
      tone: "model",
    },
    {
      level: 4,
      label: "Framework",
      primary: `PyTorch ${data.pytorchCuda.torchVersion}`,
      meta: `cuda.is_available = ${data.pytorchCuda.cudaAvailable ? "yes" : "no"} · device_count = ${data.pytorchCuda.deviceCount}`,
      badges: [
        ...(data.pytorchCuda.bf16 ? ([{ text: "bf16", variant: "good" }] as const) : []),
        { text: `cu${data.pytorchCuda.cudaVersion.replace(".", "")}`, variant: "accent" },
      ],
      tone: "framework",
    },
    {
      level: 3,
      label: "NCCL",
      primary: `NCCL ${data.pytorchCuda.nccl}`,
      meta: "Collective comms across GPUs",
      tone: "runtime",
    },
    {
      level: 2,
      label: "cuDNN",
      primary: `cuDNN v${data.pytorchCuda.cudnn}`,
      meta: "Deep-learning primitives",
      tone: "runtime",
    },
    {
      level: 1,
      label: "CUDA Runtime",
      primary: `CUDA ${data.pytorchCuda.cudaVersion}`,
      meta: "Device & memory APIs",
      tone: "runtime",
    },
    {
      level: 0,
      label: "Driver",
      primary: `NVIDIA ${data.toolchain.nvidiaDriver}`,
      meta: "Kernel module bridge to silicon",
      tone: "kernel",
    },
  ];

  const ToneClass: Record<NonNullable<Layer["tone"]>, string> = {
    kernel: "from-[var(--color-bg-deep)] to-[var(--color-bg-panel)] border-[var(--color-border)]",
    runtime: "from-[var(--color-bg-panel)] to-[var(--color-bg-elev)] border-[var(--color-border)]",
    framework: "from-[var(--color-accent-deep)]/30 to-[var(--color-bg-elev)] border-[var(--color-accent)]/50",
    model: "from-[var(--color-accent)]/15 to-[var(--color-accent-deep)]/40 border-[var(--color-accent)]/60",
  };

  return (
    <div
      className={cn(
        "relative panel p-5",
        data.vendoredCuda.warning && "border-[var(--color-warn)]/60",
      )}
      style={
        data.vendoredCuda.warning
          ? { animation: "pulse-glow 2.4s ease-in-out infinite" }
          : undefined
      }
    >
      {/* Inverted ziggurat: top deck widest so the user-facing layer dominates; reveal cascades bottom-up. */}
      <div className="flex flex-col gap-1.5">
        {layers.map((l, i) => (
          <motion.div
            key={l.level}
            initial={{ opacity: 0, x: -10 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true, margin: "0px 0px -10% 0px" }}
            transition={{ duration: 0.5, delay: STAGGER * (layers.length - i), ease: EASE_SNAP }}
            className={cn(
              "px-4 py-3 border bg-gradient-to-r flex items-center justify-between gap-3",
              ToneClass[l.tone ?? "runtime"],
            )}
            style={{ marginLeft: `${i * 8}px`, marginRight: `${i * 8}px` }}
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 mb-0.5">
                <span className="font-mono text-[9px] uppercase tracking-eyebrow text-[var(--color-fg-mute)]">
                  L{l.level} · {l.label}
                </span>
                {l.badges?.map((b) => (
                  <Badge key={b.text} variant={b.variant} size="xs">
                    {b.text}
                  </Badge>
                ))}
              </div>
              <div className="font-mono text-sm text-[var(--color-fg)] truncate">
                {l.primary}
              </div>
              {l.meta && (
                <div className="font-mono text-[10px] text-[var(--color-fg-mute)] mt-0.5 truncate">
                  {l.meta}
                </div>
              )}
            </div>
          </motion.div>
        ))}
      </div>
    </div>
  );
}

function pickVersions(data: ClusterSnapshot, names: string[]): string {
  const m = new Map(data.finetuneLibs.map((l) => [l.pkg.toLowerCase(), l.version]));
  return names
    .map((n) => {
      const v = m.get(n);
      return v ? `${n} ${v}` : null;
    })
    .filter(Boolean)
    .join("  ·  ");
}
