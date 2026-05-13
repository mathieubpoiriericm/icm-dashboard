import { useState } from "react";
import { useReducedMotion } from "@/hooks/useReducedMotion";
import { useTween } from "@/lib/motion";
import { cn } from "@/lib/cn";

interface ScrambleTextProps {
  text: string;
  duration?: number;
  delay?: number;
  className?: string;
}

const CHARS = "abcdefghijklmnopqrstuvwxyz0123456789-_./";

function randomChar(): string {
  return CHARS[Math.floor(Math.random() * CHARS.length)];
}

export function ScrambleText({
  text,
  duration = 0.9,
  delay = 0,
  className,
}: ScrambleTextProps) {
  const reduced = useReducedMotion();
  const [scrambled, setScrambled] = useState("");

  useTween(
    (t) => {
      if (t >= 1) {
        setScrambled(text);
        return;
      }
      const settled = Math.floor(t * text.length);
      let s = "";
      for (let i = 0; i < text.length; i++) {
        if (i < settled) s += text[i];
        else if (text[i] === " ") s += " ";
        else s += randomChar();
      }
      setScrambled(s);
    },
    duration,
    delay,
    [text],
  );

  const displayValue = reduced ? text : scrambled;
  return (
    <span className={cn("tabular", className)}>
      <span aria-hidden="true">{displayValue}</span>
      <span className="sr-only">{text}</span>
    </span>
  );
}
