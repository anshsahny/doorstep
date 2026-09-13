// Status icons. Each one is decorative (aria-hidden): the word beside it carries the meaning.

import type { SVGProps } from "react";
import type { Tone } from "../lib/status";

const base = (props: SVGProps<SVGSVGElement>) => ({
  width: "1.15em",
  height: "1.15em",
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2.5,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
  focusable: false,
  ...props,
});

export const Check = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="M4 12.5l5 5L20 6.5" /></svg>
);
export const Phone = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="M5 3h4l2 5-2.5 1.5a11 11 0 005 5L15 12l5 2v4a2 2 0 01-2 2A16 16 0 013 5a2 2 0 012-2z" /></svg>
);
export const Hand = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="M8 13V5.5a1.5 1.5 0 013 0V12m0-1V4.5a1.5 1.5 0 013 0V12m0-6.5a1.5 1.5 0 013 0V14a7 7 0 01-7 7h-.5a6 6 0 01-5-2.7L4 15a1.5 1.5 0 012.5-1.7L8 15" /></svg>
);
export const Alert = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="M12 3L2 21h20L12 3z" /><path d="M12 10v5M12 18h.01" /></svg>
);
export const Retry = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="M20 11a8 8 0 10-2.3 5.7" /><path d="M20 4v7h-7" /></svg>
);
export const Mic = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0014 0M12 18v3" /></svg>
);
export const Circle = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><circle cx="12" cy="12" r="7" strokeDasharray="3 3" /></svg>
);
export const Arrow = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="M5 12h14M13 6l6 6-6 6" /></svg>
);
export const Shield = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z" /></svg>
);
export const Stop = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><circle cx="12" cy="12" r="9" /><path d="M8 8l8 8" /></svg>
);

export function ToneIcon({ tone }: { tone: Tone }) {
  switch (tone) {
    case "ok":
      return <Check />;
    case "calling":
      return <Phone />;
    case "your_call":
      return <Mic />;
    case "needs_help":
      return <Hand />;
    case "no_answer":
      return <Retry />;
    case "urgent":
      return <Alert />;
    default:
      return <Circle />;
  }
}
