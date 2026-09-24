import type { WsMessage } from "./types";

type Listener = (message: WsMessage) => void;
type StatusListener = (connected: boolean) => void;

const WS_URL = import.meta.env.VITE_WS_URL ?? "ws://localhost:8000/api/ws/updates";
const RECONNECT_DELAY_MS = 3000;
const MAX_RECONNECT_DELAY_MS = 30000;

class WebSocketService {
  private socket: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private statusListeners = new Set<StatusListener>();
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private manuallyClosed = false;

  connect(): void {
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    this.manuallyClosed = false;
    this.socket = new WebSocket(WS_URL);

    this.socket.onopen = () => {
      this.reconnectAttempts = 0;
      console.info("[WS] connected");
      this.statusListeners.forEach((listener) => listener(true));
    };

    this.socket.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data) as WsMessage;
        this.listeners.forEach((listener) => listener(parsed));
      } catch (err) {
        console.error("[WS] failed to parse message", err);
      }
    };

    this.socket.onclose = () => {
      console.warn("[WS] disconnected");
      this.statusListeners.forEach((listener) => listener(false));
      if (!this.manuallyClosed) {
        this.scheduleReconnect();
      }
    };

    this.socket.onerror = (err) => {
      console.error("[WS] error", err);
      this.socket?.close();
    };
  }

  isConnected(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  /** Fires immediately with current status, then on every open/close transition. */
  subscribeStatus(listener: StatusListener): () => void {
    this.statusListeners.add(listener);
    listener(this.isConnected());
    return () => this.statusListeners.delete(listener);
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;
    const delay = Math.min(
      RECONNECT_DELAY_MS * 2 ** this.reconnectAttempts,
      MAX_RECONNECT_DELAY_MS
    );
    this.reconnectAttempts += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  disconnect(): void {
    this.manuallyClosed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.socket?.close();
    this.socket = null;
  }
}

export const websocketService = new WebSocketService();
export default websocketService;
