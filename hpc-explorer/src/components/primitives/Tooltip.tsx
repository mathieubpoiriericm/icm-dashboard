import * as RT from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export { Provider as TooltipProvider } from "@radix-ui/react-tooltip";

interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "right" | "bottom" | "left";
  align?: "start" | "center" | "end";
  delayDuration?: number;
}

export function Tooltip({
  content,
  children,
  side = "top",
  align = "center",
  delayDuration = 80,
}: TooltipProps) {
  return (
    <RT.Root delayDuration={delayDuration}>
      <RT.Trigger asChild>{children}</RT.Trigger>
      <RT.Portal>
        <RT.Content
          side={side}
          align={align}
          sideOffset={6}
          className={cn(
            "z-50 max-w-xs px-2 py-1.5 font-mono text-[10px] leading-relaxed",
            "bg-[var(--color-bg-deep)] border border-[var(--color-border-hi)]",
            "text-[var(--color-fg)] shadow-2xl",
            "data-[state=delayed-open]:animate-[fade-in_140ms_var(--ease-snap)]",
          )}
        >
          {content}
          <RT.Arrow className="fill-[var(--color-bg-deep)]" />
        </RT.Content>
      </RT.Portal>
    </RT.Root>
  );
}
