import { useProbeClock } from "@/hooks/useProbeClock";
import { formatDateISO, formatTimeOfDay, formatUptimeShort } from "@/lib/time";
import { Badge } from "@/components/primitives/Badge";

interface IdentityStripProps {
  cluster: string;
  host: string;
  jobId: string;
  partition: string;
  qos: string;
  account: string;
  probedAt: string;
  uptime: string;
  jobState: string;
}

export function IdentityStrip({
  cluster,
  host,
  jobId,
  partition,
  qos,
  account,
  probedAt,
  uptime,
  jobState,
}: IdentityStripProps) {
  const { now, uptimeSeconds } = useProbeClock(probedAt, uptime);

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2 border-b border-[var(--color-border)] bg-[var(--color-bg-deep)]/60">
      <div className="flex items-center gap-1.5 font-mono text-[11px] text-[var(--color-fg-dim)]">
        <span
          className="w-1.5 h-1.5 rounded-full bg-[var(--color-good)]"
          style={{ animation: "blink 1.4s ease-in-out infinite" }}
        />
        <span className="uppercase tracking-eyebrow text-[10px] text-[var(--color-fg-mute)]">
          telemetry
        </span>
      </div>
      <Separator />
      <div className="font-mono text-[11px]">
        <span className="text-[var(--color-fg-mute)]">cluster</span>{" "}
        <span className="text-[var(--color-accent)] font-medium">{cluster}</span>
      </div>
      <Separator />
      <div className="font-mono text-[11px]">
        <span className="text-[var(--color-fg-mute)]">host</span>{" "}
        <span className="text-[var(--color-fg)] font-medium">{host}</span>
      </div>
      <Separator />
      <div className="font-mono text-[11px]">
        <span className="text-[var(--color-fg-mute)]">job</span>{" "}
        <span className="text-[var(--color-fg)] font-medium tabular">J{jobId}</span>{" "}
        <Badge variant="good" size="xs" pulse>
          {jobState}
        </Badge>
      </div>
      <Separator />
      <div className="font-mono text-[11px]">
        <span className="text-[var(--color-fg-mute)]">partition</span>{" "}
        <span className="text-[var(--color-fg)]">{partition}</span>{" "}
        <span className="text-[var(--color-fg-mute)]">·</span>{" "}
        <span className="text-[var(--color-fg-dim)]">{qos}</span>
      </div>
      <Separator />
      <div className="font-mono text-[11px]">
        <span className="text-[var(--color-fg-mute)]">acct</span>{" "}
        <span className="text-[var(--color-fg-dim)]">{account}</span>
      </div>

      <div className="ml-auto flex items-center gap-3">
        <div className="font-mono text-[11px] text-right">
          <div className="text-[var(--color-fg-mute)] uppercase tracking-eyebrow text-[9px]">
            uptime
          </div>
          <div className="text-[var(--color-fg-dim)] tabular">
            {formatUptimeShort(uptimeSeconds)}
          </div>
        </div>
        <Separator />
        <div className="font-mono text-right">
          <div className="text-[var(--color-fg-mute)] uppercase tracking-eyebrow text-[9px]">
            {formatDateISO(now)} CEST
          </div>
          <div className="text-[var(--color-accent)] tabular text-base font-medium leading-none">
            {formatTimeOfDay(now)}
          </div>
        </div>
      </div>
    </div>
  );
}

function Separator() {
  return <div className="w-px h-4 bg-[var(--color-border)]" />;
}
