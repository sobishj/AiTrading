import { useEffect } from "react";

interface UpdateNotificationProps {
  message: string | null;
  onDismiss: () => void;
}

const AUTO_DISMISS_MS = 12000;

export default function UpdateNotification({ message, onDismiss }: UpdateNotificationProps) {
  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(onDismiss, AUTO_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [message, onDismiss]);

  if (!message) return null;

  return (
    <div className="fixed bottom-5 left-1/2 -translate-x-1/2 z-50 fade-in">
      <div className="glass-panel bg-base-900/90 px-4 py-3 flex items-center gap-3 shadow-glow border-neon-blue/30 max-w-xl">
        <span className="w-2 h-2 rounded-full bg-neon-blue pulse-glow shrink-0" />
        <p className="text-sm text-slate-200">{message}</p>
        <button onClick={onDismiss} className="text-slate-500 hover:text-slate-200 text-xs ml-2" aria-label="Dismiss">
          ✕
        </button>
      </div>
    </div>
  );
}
