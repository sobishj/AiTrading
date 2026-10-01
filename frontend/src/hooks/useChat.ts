import { useCallback, useEffect, useState } from "react";
import apiService from "../services/api";
import type { ChatMessage, ChatResponse } from "../services/types";

interface UseChatResult {
  messages: ChatMessage[];
  sending: boolean;
  loading: boolean;
  error: string | null;
  /** Today's date (YYYY-MM-DD, IST, from the server). */
  today: string;
  /** Days that have chat, for the calendar. */
  chatDays: Set<string>;
  sendMessage: (content: string) => Promise<void>;
  /** Deletes the chat of the day being viewed. */
  clearDay: () => Promise<void>;
}

function toMessages(row: ChatResponse): ChatMessage[] {
  const key = row.id ?? row.timestamp;
  return [
    { id: `u-${key}`, role: "user", content: row.user_message, timestamp: row.timestamp, stockContext: row.stock_context },
    { id: `a-${key}`, role: "assistant", content: row.ai_response, timestamp: row.timestamp, stockContext: row.stock_context,
      tradeProposal: row.trade_proposal ?? null, answeredBy: row.answered_by ?? null },
  ];
}

/** Browser-local YYYY-MM-DD; only a fallback until the server says what "today" is in IST. */
export function localDay(d = new Date()): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/**
 * ChatGPT-style conversation, one per day, backed by the server's chat history.
 * `day` (YYYY-MM-DD) picks which day to show; null = today. Only today's chat
 * takes new messages — past days are read-only. `stockContext` is the selected
 * stock, sent with each message.
 */
export function useChat(stockContext?: string | null, day?: string | null): UseChatResult {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [today, setToday] = useState(localDay());
  const [chatDays, setChatDays] = useState<Set<string>>(new Set());

  const loadDays = useCallback(() => {
    apiService.getChatDays()
      .then((r) => { setToday(r.today); setChatDays(new Set(r.days)); })
      .catch(() => { /* the calendar just won't mark days */ });
  }, []);
  useEffect(() => { loadDays(); }, [loadDays]);

  const shown = day ?? today;
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    apiService
      .getChatHistory(50, shown)
      .then((rows) => { if (!cancelled) setMessages(rows.flatMap(toMessages)); })
      .catch(() => { if (!cancelled) setMessages([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [shown]);

  const sendMessage = useCallback(
    async (content: string) => {
      const trimmed = content.trim();
      if (!trimmed || shown !== today) return;

      const pendingId = `pending-${Date.now()}`;
      setMessages((prev) => [
        ...prev,
        { id: pendingId, role: "user", content: trimmed, timestamp: new Date().toISOString(), stockContext },
      ]);
      setSending(true);
      setError(null);

      try {
        const response = await apiService.sendChatMessage(trimmed, stockContext);
        setMessages((prev) => [...prev.filter((m) => m.id !== pendingId), ...toMessages(response)]);
        setChatDays((prev) => new Set(prev).add(today));
        loadDays();   // the app may have stayed open past midnight: re-sync "today" with the server
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to send message");
      } finally {
        setSending(false);
      }
    },
    [stockContext, shown, today, loadDays]
  );

  const clearDay = useCallback(async () => {
    await apiService.clearChatHistory(shown);
    setMessages([]);
    setChatDays((prev) => { const next = new Set(prev); next.delete(shown); return next; });
  }, [shown]);

  return { messages, sending, loading, error, today, chatDays, sendMessage, clearDay };
}
