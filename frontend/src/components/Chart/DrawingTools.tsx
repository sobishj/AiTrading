export type DrawingTool = "none" | "trendline" | "horizontal";

const TOOLS: { key: DrawingTool; label: string; hint: string }[] = [
  { key: "none", label: "Cursor", hint: "Pan and inspect" },
  { key: "trendline", label: "Trendline", hint: "Click two points" },
  { key: "horizontal", label: "Level", hint: "Click a price to draw a horizontal level" },
];

interface DrawingToolsProps {
  active: DrawingTool;
  onChange: (tool: DrawingTool) => void;
  onClear: () => void;
  hasDrawings: boolean;
}

/** Drawings are saved per stock and timeframe in this browser. */
export default function DrawingTools({ active, onChange, onClear, hasDrawings }: DrawingToolsProps) {
  return (
    <div className="flex items-center gap-1">
      {TOOLS.map((tool) => (
        <button
          key={tool.key}
          title={tool.hint}
          onClick={() => onChange(tool.key)}
          className={`px-2 py-1 rounded-lg text-xs font-medium transition-all duration-150 ${
            active === tool.key
              ? "bg-neon-purple/15 text-neon-purple border border-neon-purple/30"
              : "text-slate-400 hover:text-slate-200 hover:bg-white/5 border border-transparent"
          }`}
        >
          {tool.label}
        </button>
      ))}
      {hasDrawings && (
        <button
          onClick={onClear}
          title="Remove all drawings on this chart"
          className="px-2 py-1 rounded-lg text-xs text-slate-500 hover:text-neon-rose hover:bg-white/5"
        >
          Clear
        </button>
      )}
    </div>
  );
}
