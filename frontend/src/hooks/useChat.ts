import { useCallback, useEffect, useState } from "react";
import apiService from "../services/api";
import type { ChatMessage, ChatResponse } from "../services/types";

interface UseChatResult {
  messages: ChatMessage[];
  sending: boolean;
  error: string | null;
  sendMessage: (content: string) => Promise<void>;
  clear: () => Promise<void>;
}

function toMessages(row: ChatResponse): ChatMessage[] {
  const key = row.id ?? row.timestamp;
  return [
    { id: `u-${key}`, role: "user", content: row.user_message, timestamp: row.timestamp, stockContext: row.stock_context },
    { id: `a-${key}`, role: "assistant", content: row.ai_response, timestamp: row.timestamp, stockContext: row.stock_context },
  ];
}

/**
 * ChatGPT-style conversation backed by the server's chat history, so the
 * conversation survives reloads and the coach remembers earlier turns.
 * `stockContext` is the currently selected stock, sent with each message.
 */
export function useChat(stockContext?: string | null): UseChatResult {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiService
      .getChatHistory(50)
      .then((rows) => setMessages(rows.flatMap(toMessages)))
      .catch(() => {
        /* history is a convenience; an empty chat is fine */
      });
  }, []);

  const sendMessage = useCallback(
    async (content: string) => {
      const trimmed = content.trim();
      if (!trimmed) return;

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
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to send message");
      } finally {
        setSending(false);
      }
    },
    [stockContext]
  );

  const clear = useCallback(async () => {
    await apiService.clearChatHistory();
    setMessages([]);
  }, []);

  return { messages, sending, error, sendMessage, clear };
}
