import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { MouseEvent, ReactNode } from "react";

const WIDTH = 288;
const GAP = 8;

/**
 * A small "i" badge that explains the text next to it on hover or keyboard focus.
 * The tip is portalled to <body> and positioned against the viewport: inside a
 * modal, overflow and backdrop-filter would otherwise clip and offset it.
 */
export default function InfoTip({ children, label = "More information" }: { children: ReactNode; label?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number; above: boolean } | null>(null);

  const show = () => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    const left = Math.min(Math.max(GAP, r.left + r.width / 2 - WIDTH / 2), window.innerWidth - WIDTH - GAP);
    const above = r.top > window.innerHeight / 2;
    setPos({ left, top: above ? r.top - GAP : r.bottom + GAP, above });
  };
  const hide = () => setPos(null);
  // Inside a <label>, a click would otherwise toggle/focus the field.
  const stop = (e: MouseEvent) => { e.preventDefault(); e.stopPropagation(); };

  return (
    <span
      ref={ref}
      role="button"
      tabIndex={0}
      aria-label={label}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      onClick={stop}
      className="inline-flex items-center justify-center w-3.5 h-3.5 ml-1 rounded-full border border-slate-400 text-[9px] font-semibold leading-none text-slate-400 cursor-help align-middle select-none hover:border-neon-blue hover:text-neon-blue focus:outline-none focus:border-neon-blue focus:text-neon-blue"
    >
      i
      {pos && createPortal(
        <span
          role="tooltip"
          style={{ position: "fixed", left: pos.left, top: pos.top, width: WIDTH,
                   transform: pos.above ? "translateY(-100%)" : undefined }}
          className="z-[1000] rounded-lg bg-slate-900 text-slate-100 text-[11px] font-normal leading-snug normal-case tracking-normal text-left px-3 py-2 shadow-xl pointer-events-none whitespace-normal"
        >
          {children}
        </span>,
        document.body,
      )}
    </span>
  );
}
