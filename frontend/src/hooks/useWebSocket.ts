import { useEffect, useRef, useState } from "react";
import websocketService from "../services/websocket";
import type { WsMessage } from "../services/types";

interface UseWebSocketResult {
  lastMessage: WsMessage | null;
  /** True once the socket itself is open — independent of whether any message has arrived yet. */
  connected: boolean;
}

export function useWebSocket(onMessage?: (message: WsMessage) => void): UseWebSocketResult {
  const [lastMessage, setLastMessage] = useState<WsMessage | null>(null);
  const [connected, setConnected] = useState(false);
  const callbackRef = useRef(onMessage);
  callbackRef.current = onMessage;

  useEffect(() => {
    websocketService.connect();
    const unsubscribeMessages = websocketService.subscribe((message) => {
      setLastMessage(message);
      callbackRef.current?.(message);
    });
    const unsubscribeStatus = websocketService.subscribeStatus(setConnected);

    return () => {
      unsubscribeMessages();
      unsubscribeStatus();
    };
  }, []);

  return { lastMessage, connected };
}
