import type { Stock } from "../../services/types";

interface StockListItemProps {
  stock: Stock;
  selected: boolean;
  moved?: "up" | "down";
  onSelect: (symbol: string) => void;
  /** Present in Manual mode: shows a remove button. */
  onRemove?: (symbol: string) => void;
}

// Same colours as the trade plan's action badge.
const ACTION_BADGE: Record<string, string> = {
  BUY: "text-neon-emerald bg-neon-emerald/10 border-neon-emerald/40",
  WAIT: "text-amber-300 bg-amber-300/10 border-amber-300/30",
  AVOID: "text-neon-rose bg-neon-rose/10 border-neon-rose/30",
};

export default function StockListItem({ stock, selected, moved, onSelect, onRemove }: StockListItemProps) {
  const moveClass = moved === "up" ? "rank-flash-up" : moved === "down" ? "rank-flash-down" : "";

  return (
    <div
      className={`group flex items-center rounded-xl transition-all duration-300 ${moveClass} ${
        selected
          ? "bg-gradient-to-r from-neon-blue/15 to-neon-purple/10 border border-neon-blue/30 shadow-glow"
          : "border border-transparent hover:bg-white/5 hover:border-white/10"
      }`}
    >
      <button
        onClick={() => onSelect(stock.symbol)}
        title={stock.change_reason ?? `${stock.name} (NSE:${stock.symbol})`}
        className="flex-1 min-w-0 flex items-center gap-2 text-left px-3.5 py-2"
      >
        <span className={`flex-1 truncate text-sm font-medium ${selected ? "text-neon-blue" : "text-slate-200"}`}>
          {stock.name}
        </span>
        {stock.action && (
          <span
            className={`shrink-0 rounded border px-1.5 py-px text-[10px] font-semibold tracking-wide ${ACTION_BADGE[stock.action] ?? ""}`}
            title={stock.conviction_score !== null ? `${stock.action} · conviction ${stock.conviction_score.toFixed(0)}/100` : stock.action}
          >
            {stock.action}
          </span>
        )}
      </button>
      {onRemove && (
        <button
          onClick={() => onRemove(stock.symbol)}
          aria-label={`Remove ${stock.name} from your list`}
          title="Remove from your list"
          className={`mr-2 w-6 h-6 shrink-0 rounded-md text-xs text-slate-500 hover:text-neon-rose hover:bg-neon-rose/10
            transition-opacity ${selected ? "opacity-100" : "opacity-0 group-hover:opacity-100 focus:opacity-100"}`}
        >
          ✕
        </button>
      )}
    </div>
  );
}
