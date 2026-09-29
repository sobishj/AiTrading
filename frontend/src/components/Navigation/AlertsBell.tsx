import { useEffect, useRef, useState } from "react";
import apiService from "../../services/api";
import type { PositionAlert } from "../../services/types";

interface AlertsBellProps {
  /** Bumped when new alerts arrive over WebSocket. */
  refreshKey: number;
  onSelect: (symbol: string) => void;
}

const SEVERITY: Record<string, string> = {
  critical: "border-neon-rose/40 bg-neon-rose/10",
  warning: "border-amber-300/40 bg-amber-300/5",
  info: "border-neon-blue/30 bg-neon-blue/5",
};

/** Holding alerts: unread count on a bell, the list in a dropdown. */
export default function AlertsBell({ refreshKey, onSelect }: AlertsBellProps) {
  const [open, setOpen] = useState(false);
  const [alerts, setAlerts] = useState<PositionAlert[]>([]);
  const [unread, setUnread] = useState(0);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    apiService.getAlerts(30).then((d) => {
      setAlerts(d.alerts);
      setUnread(d.unread);
    }).catch(() => {});
  }, [refreshKey, open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const ackAll = async () => {
    await apiService.acknowledgeAlerts();
    setUnread(0);
    setAlerts((a) => a.map((x) => ({ ...x, acknowledged: true })));
  };

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="relative btn-secondary px-2.5 py-1.5 text-sm"
        aria-label={`Holding alerts${unread ? ` (${unread} unread)` : ""}`}
        title="Holding alerts"
      >
        🔔
        {unread > 0 && (
          <span className="absolute -top-1 -right-1 min-w-[18px] h-[18px] px-1 rounded-full bg-neon-rose text-onaccent text-[10px] font-bold flex items-center justify-center">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-2 w-96 max-h-[70vh] overflow-y-auto glass-panel bg-base-900/95 p-2 z-50 shadow-xl">
          <div className="flex items-center justify-between px-2 py-1">
            <span className="text-xs uppercase tracking-widest text-slate-400">Holding alerts</span>
            {unread > 0 && <button onClick={ackAll} className="text-[11px] text-neon-blue hover:underline">Mark all read</button>}
          </div>
          {alerts.length === 0 && (
            <p className="text-xs text-slate-500 px-2 py-3">No alerts yet. Add holdings and the AI will watch them for you.</p>
          )}
          {alerts.map((a) => (
            <button
              key={a.id}
              onClick={() => {
                onSelect(a.symbol);
                setOpen(false);
              }}
              className={`w-full text-left rounded-lg border px-3 py-2 mb-1 ${SEVERITY[a.severity] ?? SEVERITY.info} ${a.acknowledged ? "opacity-60" : ""}`}
            >
              <div className="flex justify-between gap-2">
                <span className="text-xs font-semibold text-slate-200">{a.title}</span>
                <span className="text-[10px] text-slate-500 shrink-0">
                  {new Date(a.created_at + "Z").toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}
                </span>
              </div>
              <p className="text-[11px] text-slate-400 mt-0.5">{a.message}</p>
              {a.correct !== null && (
                <p className={`text-[10px] mt-0.5 ${a.correct ? "text-neon-emerald" : "text-slate-500"}`}>
                  {a.correct ? "✓ The warning proved right" : "The price held up after this warning"}
                </p>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
