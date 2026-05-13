import { useState } from "react";
import { useReducedMotion } from "@/hooks/useReducedMotion";
import { useTween } from "@/lib/motion";
import { cn } from "@/lib/cn";

interface CounterProps {
  to: number;
  duration?: number;
  delay?: number;
  fractionDigits?: number;
  prefix?: string;
  suffix?: string;
  className?: string;
}

export function Counter({
  to,
  duration = 0.9,
  delay = 0,
  fractionDigits = 0,
  prefix = "",
  suffix = "",
  className,
}: CounterProps) {
  const reduced = useReducedMotion();
  const [animated, setAnimated] = useState(0);

  useTween((t) => setAnimated((1 - Math.pow(1 - t, 3)) * to), duration, delay, [to]);

  const displayValue = reduced ? to : animated;
  return (
    <span className={cn("tabular", className)}>
      {prefix}
      {displayValue.toFixed(fractionDigits)}
      {suffix}
    </span>
  );
}
