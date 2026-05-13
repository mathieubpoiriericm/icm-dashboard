import { useMemo } from "react";
import { motion } from "motion/react";
import { cn } from "@/lib/cn";
import { EASE_SNAP } from "@/lib/motion";

interface SparklineProps {
  values: number[];
  width?: number;
  height?: number;
  color?: string;
  filled?: boolean;
  animate?: boolean;
  className?: string;
}

export function Sparkline({
  values,
  width = 80,
  height = 18,
  color = "var(--color-accent)",
  filled = true,
  animate = true,
  className,
}: SparklineProps) {
  const path = useMemo(() => {
    if (values.length < 2) return { line: "", area: "", lastY: height };
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    const stepX = width / (values.length - 1);
    const points = values.map((v, i) => {
      const x = i * stepX;
      const y = height - ((v - min) / range) * (height - 2) - 1;
      return [x, y] as const;
    });
    const [lastX, lastY] = points[points.length - 1];
    const line = points
      .map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`)
      .join(" ");
    const area = `${line} L${lastX.toFixed(1)},${height} L0,${height} Z`;
    return { line, area, lastY };
  }, [values, width, height]);

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className={cn("overflow-visible", className)}
      aria-hidden
    >
      {filled && (
        <motion.path
          d={path.area}
          fill={color}
          fillOpacity={0.15}
          initial={animate ? { opacity: 0 } : false}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6, delay: 0.2 }}
        />
      )}
      <motion.path
        d={path.line}
        fill="none"
        stroke={color}
        strokeWidth={1.25}
        strokeLinecap="round"
        strokeLinejoin="round"
        pathLength={1}
        initial={animate ? { pathLength: 0 } : false}
        animate={{ pathLength: 1 }}
        transition={{ duration: 0.9, ease: EASE_SNAP }}
      />
      {values.length > 1 && (
        <circle cx={width} cy={path.lastY} r={1.5} fill={color} />
      )}
    </svg>
  );
}
