// =============================================================================
// FORMAT HELPERS
// =============================================================================

// Bare numbers are treated as bytes so a missing unit cannot inflate to GB.
const MEM_FACTORS: Record<string, number> = {
  B: 1 / 1024 ** 3,
  KB: 1 / 1024 ** 2,
  MB: 1 / 1024,
  GB: 1,
  TB: 1024,
  PB: 1024 ** 2,
};

export function parseMemoryGB(s: string): number {
  const m = s.trim().match(/^([\d.]+)\s*(B|KB|MB|GB|TB|PB)?$/i);
  if (!m) return 0;
  const v = parseFloat(m[1]);
  const u = (m[2] || "B").toUpperCase();
  return v * (MEM_FACTORS[u] ?? 0);
}

export function formatGB(gb: number, fractionDigits = 0): string {
  if (gb >= 1024) return `${(gb / 1024).toFixed(fractionDigits || 1)} TB`;
  if (gb >= 1) return `${gb.toFixed(fractionDigits)} GB`;
  if (gb >= 1 / 1024) return `${(gb * 1024).toFixed(fractionDigits)} MB`;
  return `${(gb * 1024 * 1024).toFixed(fractionDigits)} KB`;
}

export function formatPct(n: number, fractionDigits = 0): string {
  return `${n.toFixed(fractionDigits)}%`;
}

export function formatRatio(value: number): string {
  return value.toFixed(3);
}

export function compactNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toString();
}

export function clamp(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, n));
}

export function groupBy<T, K>(items: Iterable<T>, keyFn: (item: T) => K): Map<K, T[]> {
  const out = new Map<K, T[]>();
  for (const item of items) {
    const key = keyFn(item);
    const arr = out.get(key);
    if (arr) arr.push(item);
    else out.set(key, [item]);
  }
  return out;
}
