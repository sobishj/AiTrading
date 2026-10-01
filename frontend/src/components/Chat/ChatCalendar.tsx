import { useEffect, useMemo, useRef, useState } from "react";

interface ChatCalendarProps {
  /** Day being viewed, YYYY-MM-DD. */
  value: string;
  /** Today in IST, YYYY-MM-DD; later days can't be picked. */
  today: string;
  /** Days that have chat (marked with a dot). */
  chatDays: Set<string>;
  onChange: (day: string) => void;
}

const WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

const parse = (iso: string) => {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
};
const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const longLabel = (day: string) =>
  parse(day).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", year: "numeric" });

/** Date button for the chat ("Today · Thu, 1 Oct 2026"); opens a month calendar with future days disabled. */
export default function ChatCalendar({ value, today, chatDays, onChange }: ChatCalendarProps) {
  const [open, setOpen] = useState(false);
  const [month, setMonth] = useState(() => { const d = parse(value); return new Date(d.getFullYear(), d.getMonth(), 1); });
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const d = parse(value);
    setMonth(new Date(d.getFullYear(), d.getMonth(), 1));
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open, value]);

  const cells = useMemo(() => {
    const first = new Date(month.getFullYear(), month.getMonth(), 1);
    const lead = (first.getDay() + 6) % 7;   // Monday-first grid
    const days = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
    return [...Array(lead).fill(null),
      ...Array.from({ length: days }, (_, i) => iso(new Date(month.getFullYear(), month.getMonth(), i + 1)))];
  }, [month]);

  const todayDate = parse(today);
  const atCurrentMonth = month.getFullYear() === todayDate.getFullYear() && month.getMonth() === todayDate.getMonth();
  const pick = (day: string) => { onChange(day); setOpen(false); };

  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen((o) => !o)} aria-haspopup="dialog" aria-expanded={open}
        className="flex items-center gap-1.5 text-[11px] px-2 py-1 rounded-lg border border-white/10 text-slate-300 hover:border-neon-blue/40 hover:text-neon-blue"
        title="Show the chat of another day">
        <span aria-hidden>📅</span>
        {value === today ? <><span className="font-semibold text-neon-blue">Today</span> · {longLabel(value)}</> : longLabel(value)}
      </button>

      {open && (
        <div role="dialog" aria-label="Choose a chat day"
          className="absolute right-0 top-full mt-1 z-40 w-64 glass-panel bg-base-900/95 p-3 shadow-xl">
          <div className="flex items-center justify-between mb-2">
            <button onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}
              className="px-2 text-slate-400 hover:text-slate-100" aria-label="Previous month">‹</button>
            <span className="text-xs font-semibold text-slate-200">
              {month.toLocaleDateString("en-IN", { month: "long", year: "numeric" })}
            </span>
            <button onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}
              disabled={atCurrentMonth} className="px-2 text-slate-400 hover:text-slate-100 disabled:opacity-20 disabled:cursor-not-allowed"
              aria-label="Next month">›</button>
          </div>
          <div className="grid grid-cols-7 gap-0.5 text-center">
            {WEEKDAYS.map((w) => <div key={w} className="text-[10px] text-slate-500 py-1">{w}</div>)}
            {cells.map((day, i) => {
              if (!day) return <div key={`blank-${i}`} />;
              const future = day > today;
              const selected = day === value;
              return (
                <button key={day} disabled={future} onClick={() => pick(day)}
                  title={future ? "Future dates have no chat" : chatDays.has(day) ? "Has chat" : "No chat this day"}
                  className={`relative h-8 rounded-md text-xs transition-colors
                    ${selected ? "bg-neon-blue/25 text-neon-blue font-semibold" : ""}
                    ${!selected && day === today ? "border border-neon-blue/40 text-slate-100" : ""}
                    ${future ? "text-slate-600 cursor-not-allowed" : !selected ? "text-slate-300 hover:bg-white/10" : ""}`}>
                  {Number(day.slice(8))}
                  {chatDays.has(day) && <span className="absolute bottom-1 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full bg-neon-emerald" />}
                </button>
              );
            })}
          </div>
          <div className="mt-2 flex items-center justify-between text-[10px] text-slate-500">
            <span className="flex items-center gap-1"><span className="w-1 h-1 rounded-full bg-neon-emerald" /> has chat</span>
            {value !== today && <button onClick={() => pick(today)} className="text-neon-blue hover:underline">Go to today</button>}
          </div>
        </div>
      )}
    </div>
  );
}
