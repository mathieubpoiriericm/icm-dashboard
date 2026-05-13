import { useEffect, useMemo, useState } from "react";
import { parseProbedAt, parseUptime } from "@/lib/time";

interface ProbeClock {
  now: Date;
  uptimeSeconds: number;
}

/**
 * Returns a Date that starts at the probe's CEST timestamp and advances in real time.
 * The uptime advances alongside, so animated counters stay in sync.
 */
export function useProbeClock(probedAt: string, uptimeStart: string): ProbeClock {
  const startDate = useMemo(() => parseProbedAt(probedAt) ?? new Date(), [probedAt]);
  const startUptime = useMemo(() => parseUptime(uptimeStart), [uptimeStart]);

  const [state, setState] = useState<ProbeClock>(() => ({
    now: startDate,
    uptimeSeconds: startUptime,
  }));

  useEffect(() => {
    const t0 = performance.now();
    const id = window.setInterval(() => {
      const elapsedMs = performance.now() - t0;
      setState({
        now: new Date(startDate.getTime() + elapsedMs),
        uptimeSeconds: startUptime + elapsedMs / 1000,
      });
    }, 1000);
    return () => window.clearInterval(id);
  }, [startDate, startUptime]);

  return state;
}
