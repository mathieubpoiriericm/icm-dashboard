import { useEffect, useRef, useState } from "react";

export const SECTION_IDS = [
  "identity",
  "nvlink",
  "cpu",
  "cuda-stack",
  "modules",
  "partitions",
  "qos-fairshare",
  "storage",
] as const;

export type SectionId = (typeof SECTION_IDS)[number];

export const SECTION_LABELS: Record<SectionId, string> = {
  identity: "identity",
  nvlink: "nvlink",
  cpu: "cpu · numa",
  "cuda-stack": "cuda stack",
  modules: "modules",
  partitions: "partitions",
  "qos-fairshare": "qos · fairshare",
  storage: "storage",
};

export type ChordTarget = SectionId | "top";

export function formatChordTarget(target: ChordTarget): string {
  if (target === "top") return "§ top";
  const num = (SECTION_IDS.indexOf(target) + 1).toString().padStart(2, "0");
  return `§ ${num} · ${SECTION_LABELS[target]}`;
}

/**
 * Vim-style chord nav: `g` then `1`-`8` jumps to a section, `g h` jumps to top.
 * Returns the most-recent target so the UI can show a brief flash if it wants.
 *
 * The chord-primed flag and the auto-reset timer live in refs (not state) so
 * the keydown effect can subscribe once: a state write would re-run the effect
 * and its cleanup would cancel the timer it had just armed.
 */
export function useSectionNav(): { lastTarget: ChordTarget | null } {
  const chordPrimed = useRef(false);
  const timerRef = useRef<number | null>(null);
  const [lastTarget, setLastTarget] = useState<ChordTarget | null>(null);

  useEffect(() => {
    const reset = () => {
      chordPrimed.current = false;
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
    const onKey = (e: KeyboardEvent) => {
      const target = e.target;
      const isInput =
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        (target instanceof HTMLElement && target.isContentEditable);
      if (isInput) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      if (e.key === "g" && !chordPrimed.current) {
        chordPrimed.current = true;
        timerRef.current = window.setTimeout(reset, 1200);
        return;
      }
      if (chordPrimed.current) {
        if (e.key === "h") {
          window.scrollTo({ top: 0, behavior: "smooth" });
          setLastTarget("top");
          reset();
          return;
        }
        const n = parseInt(e.key, 10);
        if (n >= 1 && n <= SECTION_IDS.length) {
          const id = SECTION_IDS[n - 1];
          const el = document.getElementById(id);
          if (el) {
            el.scrollIntoView({ behavior: "smooth", block: "start" });
            // Move keyboard/AT focus so screen readers announce the section.
            if (el instanceof HTMLElement) el.focus({ preventScroll: true });
            setLastTarget(id);
          }
        }
        reset();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, []);

  // Auto-clear the chord-nav target so a transient flash pill in the UI
  // can fade itself out without each consumer re-implementing the timer.
  useEffect(() => {
    if (!lastTarget) return;
    const id = window.setTimeout(() => setLastTarget(null), 1400);
    return () => window.clearTimeout(id);
  }, [lastTarget]);

  return { lastTarget };
}
