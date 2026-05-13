import { useEffect, useRef, useState } from "react";

export function useReveal<T extends Element = HTMLDivElement>(
  options?: IntersectionObserverInit,
): { ref: React.RefObject<T | null>; revealed: boolean } {
  const ref = useRef<T>(null);
  const [revealed, setRevealed] = useState(false);

  useEffect(() => {
    const node = ref.current;
    if (!node || revealed) return;
    const obs = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            setRevealed(true);
            obs.disconnect();
            break;
          }
        }
      },
      { threshold: 0.15, rootMargin: "0px 0px -10% 0px", ...options },
    );
    obs.observe(node);
    return () => obs.disconnect();
    // `options` is captured at first observe; passing a fresh literal each
    // render would needlessly tear down the observer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revealed]);

  return { ref, revealed };
}
