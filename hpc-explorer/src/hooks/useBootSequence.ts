import { useEffect, useState } from "react";
import { useReducedMotion } from "./useReducedMotion";

/**
 * Returns a boot stage 0..N-1 that increments on a fixed cadence on first mount.
 * Used to fade in the telemetry bar piece-by-piece.
 */
export function useBootSequence(stages: number, stepMs = 140): number {
  const reduced = useReducedMotion();
  const [stage, setStage] = useState(-1);

  useEffect(() => {
    if (reduced) return;
    let i = 0;
    const id = window.setInterval(() => {
      i += 1;
      setStage(i - 1);
      if (i >= stages) window.clearInterval(id);
    }, stepMs);
    return () => window.clearInterval(id);
  }, [stages, stepMs, reduced]);

  return reduced ? stages - 1 : stage;
}
