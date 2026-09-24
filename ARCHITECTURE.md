# TradeAI — Architecture

_Last updated: 2026-09-24 (v2 ranking engine)._

## Stack: PRD vs. what is built

| Concern | PRD v2.0 | Implemented |
|---|---|---|
| Desktop UI | WPF (.NET 9), MVVM | React 18 + TypeScript + Vite + Tailwind (MVVM-style: components / hooks / services) |
| Backend / workers | .NET Worker Service | FastAPI (Python 3.14) with asyncio background loops |
| AI runtime | Qwen 3 via LM Studio | Any OpenAI-compatible server at `LLM_BASE_URL` (currently Qwen 2.5 1.5B via Bionic; LM Studio + Qwen 3 is a `.env` change; `<think>` blocks are stripped) |
| Orchestrator | Semantic Kernel | Semantic Kernel (Python) |
| Database | PostgreSQL | PostgreSQL 17 + pgvector (Docker, port 5433) |
| Charts | TradingView | TradingView `lightweight-charts` 4.2 (EMA/RSI/MACD/volume computed server-side) |
| Broker / data | Zerodha Kite Connect | Kite when connected; **Yahoo Finance fallback** so the app works with no broker key |

Porting to WPF is still an open decision (see `TASKS.md`). The REST/WebSocket API is client-agnostic,
so a WPF client could replace the React one without backend changes.

## Runtime overview

```
                 ┌──────────────── background engine (main.py) ─────────────────┐
                 │ ranking loop (user cadence) · news watch (3 min) ·            │
                 │ 08:30 IST morning brief · 16:00 IST learning cycle            │
                 └───────────────┬───────────────────────────────────────────────┘
                                 ▼
 Yahoo / Kite ──► market_service ──► analysis_service ──► ranking_service ──► recommendations (market memory)
 RSS feeds    ──►  (candles, news,     (indicators, score,   (conviction, strategy,        │
 NSE FII/DII  ──►   overview, FII/DII)  setups, back-test,     evidence adjustments,       ▼
                                        trade plan)            rank reasons)          learning_service
                                                                   │                  (grading, tradebook
                                                                   ▼                   import, strategy stats,
                                          insight_service ──► API ──► WebSocket ──►    weight recalibration)
                                          (analysis sections,        React UI
                                           chat context)  ◄── llm_service (narrative only, optional)
```

## Backend modules (`backend/`)

| Module | Responsibility |
|---|---|
| `main.py` | App, CORS, Kite OAuth routes, background loops (ranking, news trigger, daily scheduler) |
| `api/routes.py` | REST + WebSocket endpoints; `refresh_and_broadcast` |
| `data_provider.py` | Yahoo Finance chart API client (cached 60 s, 8 concurrent requests) |
| `market_service.py` | Candle source selection, RSS news (ET, ET Stocks, Livemint, Business Standard), keyword-matched stock sentiment, market overview/regime, NSE FII/DII |
| `analysis_service.py` | Indicators, 0–100 technical score, setup detection, per-stock back-test, trade plan |
| `ranking_service.py` | Conviction score, strategy choice, evidence adjustments, ranking, rank-change reasons, recommendations, position sizing, weight recalibration, `AppSettings` |
| `learning_service.py` | Auto-grading of recommendations, Zerodha tradebook import, strategy performance, learning cycle |
| `brief_service.py` | Morning brief generation and storage |
| `insight_service.py` | Analysis-panel sections, cached LLM narrative, chat grounding context |
| `llm_service.py` | Semantic Kernel wrapper, availability probe, background job queue, embeddings |
| `memory_service.py` | pgvector storage/similarity of graded setups |
| `kite_service.py` | Kite auth, historical data, order-basket payloads, tradebook parsing |
| `universe.py` | Built-in 55-stock NSE large-cap universe (display names, sectors, news keywords) |
| `prompts/prompt_library.py` | All LLM prompts |

## Scoring model

**Technical score (0–100)** = trend (30) + momentum (25) + structure (20) + volume (15) +
relative strength vs NIFTY (10) − penalties (overbought RSI > 75, > 8 % above EMA 20, ATR > 5 %,
below both EMA 50 and EMA 200). Today's partial-session volume is projected to a full day before
it is compared.

**Conviction** = technical × w_t + news sentiment × w_s + volume confirmation × w_v
(weights start at 55/30/15 and are recalibrated from outcomes), plus bounded evidence adjustments:

| Adjustment | Range | Source |
|---|---|---|
| Historical edge | ±5 | Back-test of the detected setup on this stock's last ~year (1.5-ATR stop, 3-ATR target, 10 bars), needs ≥ 4 signals |
| Learned edge | ±5 | Strategy win rate from graded calls + real trades, needs ≥ 8 trades |
| Sector rotation | +3 | Stock in one of the two strongest sectors (by 20-day relative strength) and outperforming |
| Earnings momentum | +3 | Results/earnings headlines, positive sentiment and a > 1 % up day |

**Strategies** (PRD §15): Breakout, Earnings Momentum, Gap-up Continuation, Pullback to EMA,
Trend Momentum, Sector Rotation (or "No Setup").

**Trade plan:** the entry zone depends on the setup. The stop sits under the 10-day swing low, capped at
1.5 ATR from the entry midpoint. The target is 2R (1.5R for gap-ups), which is the same shape the
back-test measures. Action: **BUY** (setup + technical ≥ 55 + conviction ≥ 55), **WAIT**, or
**AVOID** (downtrend or technical < 40). The engine is long-only because NSE cash delivery can't be
held short. Risk level rises one notch in a risk-off regime. Position size = capital × risk % ÷
(entry − stop).

**Market regime:** risk-on (NIFTY uptrend and VIX < 16), risk-off (NIFTY downtrend, VIX > 20 or
a NIFTY day of −1 % or worse), otherwise neutral.

## Learning loop (PRD §14–16)

1. The top 5 BUY ideas of each ranking run are stored as recommendations with a full context
   snapshot (`context_json`). A recommendation is reused while it stays valid, so there are no
   duplicates.
2. Once its holding window ends, or earlier if the stop or target is hit, each recommendation is
   replayed against actual bars: `target_hit`, `stop_hit`, `expired_profit`, `expired_loss` or
   `not_triggered`.
3. Zerodha tradebook uploads are FIFO-matched into round trips (P&L × quantity), deduplicated by
   trade ID, and linked to the recommendation issued in the 10 days before the entry.
4. Strategy statistics feed the learned edge. Weights are recalibrated with a 70/30 blend, clamped to
   [0.10, 0.70]. When both exist, real trades override auto-grades.

## Automatic and Manual lists

- **Automatic**: stocks with `watchlist_status = 'active'` (the AI-managed universe, seeded from `universe.py`).
- **Manual**: stocks with `in_manual_list = true`, which the user adds and removes (`POST/DELETE /api/watchlist/manual`).
- The engine ranks the union of both (`models.tracked_stock_filter()`), so a Manual share gets the same indicators, plan, analysis, news triggers and chat context. `GET /api/stocks?list=auto|manual|all` returns one list in ranking order, with `list_rank` as its position within that list.

## AI analyst learning loop (`ai_analyst_service.py`)

The local LLM improves by accumulating a graded, inspectable record, never by changing its weights:

| Step | When | What |
|---|---|---|
| News reads | As headlines arrive (and at startup) | One headline per call → impact −2..+2 + reason (`ai_news_insights`). Price-tracker blogs are skipped. Recent reads replace keyword sentiment in the conviction score |
| Forecasts | `AI_FORECAST_TIME` (08:45 IST) weekdays, or "Forecast now" | Top 10 + Manual list: direction, P(up), expected move over 5 trading days (`ai_predictions`, with the exact prompt and raw answer) |
| Grading | 16:00 learning cycle | Close after 5 trading days vs price at forecast: hit/miss + Brier score |
| Reflection | After grading (≥3 new grades) | Qwen reviews recent hits/misses and rewrites ≤8 lessons (`ai_lessons`, versioned). Lessons are injected into forecasts, analysis notes and chat |
| Earned trust | Every ranking run | `ai_view` adjustment = trust × (P(up) − 50)/50, where trust = 0 until 30 graded forecasts **and** hit rate beats the best naive baseline, then 50 × edge, capped at 5 points |

| Practice | Whenever the market is closed and the LLM is idle (paused 2 min after any chat; ≤400/day) | Random stock at a random past date: indicators computed only from bars up to that day, date hidden, no news; forecast graded immediately against the real close 5 bars later (`kind='practice'`). Every 20 practice grades trigger a lesson review. Practice never earns ranking trust |
| Next-session outlook | 20:00 and 07:45 IST (`AI_OUTLOOK_TIMES`), and at startup if missing | Headlines since the close + global indices, crude, USD/INR, FII/DII → NIFTY bias, P(up), summary, up to 5 sector impacts (`ai_market_outlooks`); graded against the session's NIFTY close |

Probabilities are clamped to 10–90%. Forecasts record the lessons version they used, so the Learning window can show whether hit rate improves across versions and weeks. `GET /api/ai/training-data` exports graded forecasts as JSONL for a future LoRA fine-tune.

## Data model additions (v2)

- `stocks.keywords`, `stocks.in_manual_list`
- `recommendations.action, instrument, strategy, entry_low, entry_high, risk_reward, context_json, ai_commentary`
- `trade_history.quantity, exit_date, source ('auto'|'zerodha'), trade_ref`
- `app_settings.capital, risk_per_trade_pct`
- new tables `morning_briefs`, `ai_news_insights`, `ai_predictions` (with `kind` live/practice), `ai_lessons`, `ai_market_outlooks`; `app_settings.ai_practice_enabled`

Existing databases are migrated in place at startup (`database._COLUMN_MIGRATIONS`, additive only).

## Frontend (`frontend/src/`)

- `layouts/MainLayout.tsx`: the PRD §5 grid.
- `components/`: `Navigation/TopBar`, `StockList`, `BestPick/MorningBriefBar`, `Chart/*`,
  `TradePlan/*`, `Analysis/AnalysisPanel`, `Chat/*`, `Learning/LearningModal`.
- `hooks/`: `useStocks`, `useResource` (per-panel fetch with stale-response protection),
  `useChat` (server-backed history), `useSettings`, `useWebSocket`.
- `services/`: `api.ts` (REST), `websocket.ts` (auto-reconnect), `types.ts`.
- Live updates: a `ranking_update` over WebSocket reloads the list and bumps a version that
  refreshes the chart levels, trade plan and analysis. `morning_brief` and `learning_update` show
  toasts.

## Key decisions

| Decision | Why |
|---|---|
| Yahoo Finance fallback | Kite historical data needs a paid add-on and a daily login; without data the app is useless |
| Deterministic engine, LLM for narrative only | A 1.5B local model is slow (15–40 s per call) and invents details; rankings must be fast, reproducible and explainable |
| Back-test inside the ranking | Gives every pick stock-specific evidence immediately, before any trade history exists |
| Long-only plans | Cash-segment shorts can't be held overnight; F&O instrument selection is a later task |
| Moneycontrol RSS dropped | Its feeds stopped updating in April 2024 |
