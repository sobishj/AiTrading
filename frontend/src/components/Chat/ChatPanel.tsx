import { FormEvent, useEffect, useRef, useState } from "react";
import { useChat } from "../../hooks/useChat";
import Markdown from "./Markdown";

interface ChatPanelProps {
  symbol: string | null;
  name?: string;
  topName?: string;
  secondName?: string;
  llmAvailable: boolean;
}

/** PRD §10: ChatGPT-style Trading Coach that remembers the conversation and sees the live market. */
export default function ChatPanel({ symbol, name, topName, secondName, llmAvailable }: ChatPanelProps) {
  const { messages, sending, error, sendMessage, clear } = useChat(symbol);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

  const suggestions = [
    topName && `Why did ${topName} become number one?`,
    topName && secondName && `Compare ${topName} with ${secondName}.`,
    name && `Is the entry for ${name} still valid?`,
    "What's the market mood today?",
  ].filter(Boolean) as string[];

  const submit = (text: string) => {
    if (!text.trim() || sending) return;
    setInput("");
    sendMessage(text);
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit(input);
  };

  return (
    <div className="glass-panel h-full flex flex-col overflow-hidden">
      <div className="px-4 py-2.5 border-b border-white/5 flex items-center justify-between">
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-widest">AI Trading Coach</h2>
        <div className="flex items-center gap-3">
          {!llmAvailable && <span className="text-[11px] text-neon-rose">model offline</span>}
          {messages.length > 0 && (
            <button onClick={() => clear()} className="text-[11px] text-slate-500 hover:text-slate-300">
              New chat
            </button>
          )}
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && (
          <div className="space-y-3">
            <p className="text-xs text-slate-500">
              Ask about the ranking, a setup, or your trades. I can see the live ranking, trade plans, news and
              your uploaded trade history.
            </p>
            <div className="flex flex-wrap gap-2">
              {suggestions.map((s) => (
                <button
                  key={s}
                  onClick={() => submit(s)}
                  className="text-xs px-3 py-1.5 rounded-full border border-white/10 text-slate-300 hover:border-neon-blue/40 hover:text-neon-blue transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[88%] rounded-xl px-3 py-2 text-sm leading-relaxed fade-in ${
                m.role === "user"
                  ? "bg-gradient-to-r from-neon-blue/20 to-neon-purple/20 text-slate-100"
                  : "bg-white/5 text-slate-300 border border-white/5"
              }`}
            >
              {m.role === "assistant" ? <Markdown text={m.content} /> : m.content}
            </div>
          </div>
        ))}
        {sending && (
          <div className="text-xs text-slate-500 animate-pulse">
            TradeAI is thinking… (the local model can take up to a minute)
          </div>
        )}
        {error && <div className="text-xs text-neon-rose">{error}</div>}
      </div>

      <form onSubmit={handleSubmit} className="p-3 border-t border-white/5 flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={name ? `Ask about ${name} or the market…` : "Ask about the market…"}
          className="flex-1 bg-base-800/80 border border-white/10 rounded-xl px-3.5 py-2 text-sm
            placeholder:text-slate-500 focus:outline-none focus:border-neon-blue/50 transition-all duration-150"
        />
        <button type="submit" disabled={sending || !input.trim()} className="btn-primary disabled:opacity-40">
          Send
        </button>
      </form>
    </div>
  );
}
