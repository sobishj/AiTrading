import { useState } from "react";
import apiService from "../../services/api";

interface ActionButtonsProps {
  symbol: string;
  canPrepareOrder: boolean;
}

const KITE_BASKET_URL = "https://kite.zerodha.com/connect/basket";

/**
 * Open the stock on Zerodha, or prepare the order via Kite Publisher. TradeAI
 * never places orders: Publisher opens Zerodha's own order window, where the
 * user reviews and confirms (PRD §17).
 */
export default function ActionButtons({ symbol, canPrepareOrder }: ActionButtonsProps) {
  const [preparing, setPreparing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  // Same deep link as backend kite_service.generate_trade_url().
  const kiteUrl = `https://kite.zerodha.com/dashboard#stocks/nse/${encodeURIComponent(symbol.toUpperCase())}`;
  const tradingViewUrl = `https://www.tradingview.com/chart/?symbol=NSE:${encodeURIComponent(symbol.toUpperCase())}`;

  const prepareOrder = async () => {
    setPreparing(true);
    setMessage(null);
    try {
      const basket = await apiService.getOrderBasket(symbol);
      if (!basket.publisher_available || !basket.api_key) {
        setMessage("Set KITE_API_KEY in backend/.env to prepare orders on Zerodha. Opening the stock instead.");
        window.open(kiteUrl, "_blank", "noopener,noreferrer");
        return;
      }
      const form = document.createElement("form");
      form.method = "POST";
      form.action = KITE_BASKET_URL;
      form.target = "_blank";
      for (const [name, value] of Object.entries({ api_key: basket.api_key, data: JSON.stringify(basket.basket) })) {
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = name;
        input.value = value;
        form.appendChild(input);
      }
      document.body.appendChild(form);
      form.submit();
      form.remove();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Could not prepare the order");
    } finally {
      setPreparing(false);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <a href={kiteUrl} target="_blank" rel="noopener noreferrer" className="btn-primary flex-1 text-center">
          Open in Zerodha
        </a>
        <a href={tradingViewUrl} target="_blank" rel="noopener noreferrer" className="btn-secondary text-center" title="Open in TradingView">
          TV
        </a>
      </div>
      {canPrepareOrder && (
        <button onClick={prepareOrder} disabled={preparing} className="btn-secondary w-full disabled:opacity-50">
          {preparing ? "Preparing…" : "Prepare order on Zerodha"}
        </button>
      )}
      {message && <p className="text-[11px] text-slate-400">{message}</p>}
    </div>
  );
}
