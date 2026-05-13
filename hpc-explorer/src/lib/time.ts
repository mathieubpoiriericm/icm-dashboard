// =============================================================================
// TIME HELPERS
// Parse the probe's CEST timestamp and uptime, advance them client-side.
// =============================================================================

const TZ_OFFSETS: Record<string, number> = {
  CET: 60,
  CEST: 120,
  UTC: 0,
  GMT: 0,
  EST: -300,
  EDT: -240,
  PST: -480,
  PDT: -420,
};

/**
 * Parse "2026-05-10 15:52:05 CEST" into a Date (UTC-anchored).
 * Returns null if unparseable.
 */
export function parseProbedAt(s: string): Date | null {
  const m = s.match(/(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})\s+(\w+)/);
  if (!m) return null;
  const [, y, mo, d, hh, mm, ss, tz] = m;
  const offset = TZ_OFFSETS[tz] ?? 0;
  const utc = Date.UTC(+y, +mo - 1, +d, +hh, +mm, +ss) - offset * 60_000;
  return new Date(utc);
}

/**
 * Parse "up 4 days, 8 hours, 17 minutes" → total seconds. Lossy on weeks/months.
 */
export function parseUptime(s: string): number {
  let total = 0;
  for (const m of s.matchAll(/(\d+)\s*(day|hour|minute|second)s?/gi)) {
    const n = +m[1];
    const unit = m[2].toLowerCase();
    if (unit.startsWith("day")) total += n * 86400;
    else if (unit.startsWith("hour")) total += n * 3600;
    else if (unit.startsWith("minute")) total += n * 60;
    else total += n;
  }
  return total;
}

export function formatUptimeShort(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (d > 0) return `${d}d ${h}h ${m}m ${s.toString().padStart(2, "0")}s`;
  if (h > 0) return `${h}h ${m}m ${s.toString().padStart(2, "0")}s`;
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

export function formatTimeOfDay(d: Date, tz = "CEST"): string {
  const offset = TZ_OFFSETS[tz] ?? 0;
  const local = new Date(d.getTime() + offset * 60_000);
  const hh = local.getUTCHours().toString().padStart(2, "0");
  const mm = local.getUTCMinutes().toString().padStart(2, "0");
  const ss = local.getUTCSeconds().toString().padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

export function formatDateISO(d: Date, tz = "CEST"): string {
  const offset = TZ_OFFSETS[tz] ?? 0;
  const local = new Date(d.getTime() + offset * 60_000);
  const y = local.getUTCFullYear();
  const mo = (local.getUTCMonth() + 1).toString().padStart(2, "0");
  const dd = local.getUTCDate().toString().padStart(2, "0");
  return `${y}-${mo}-${dd}`;
}
