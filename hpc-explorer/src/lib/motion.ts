// =============================================================================
// MOTION HELPERS
// Shared easing curve + a tiny RAF-driven tween hook (used by Counter and
// ScrambleText). Honours `prefers-reduced-motion` by skipping the animation.
// =============================================================================

import { useEffect } from "react";
import { useReducedMotion } from "@/hooks/useReducedMotion";

export const EASE_SNAP = [0.16, 1, 0.3, 1] as const;

export const STAGGER = 0.07;
export const STAGGER_GROUP = 0.2;

export function useTween(
  onTick: (t: number) => void,
  duration: number,
  delay: number,
  deps: unknown[],
) {
  const reduced = useReducedMotion();

  useEffect(() => {
    if (reduced) return;
    let rafId = 0;
    const start = performance.now() + delay * 1000;
    const total = duration * 1000;
    const tick = (now: number) => {
      if (now < start) {
        rafId = requestAnimationFrame(tick);
        return;
      }
      const t = Math.min(1, (now - start) / total);
      onTick(t);
      if (t < 1) rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [duration, delay, reduced, ...deps]);
}
