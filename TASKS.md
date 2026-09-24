# TradeAI — Tasks

_Last updated: 2026-09-24._ Status against PRD v2.0.

## Done (v2)

- [x] Free market-data fallback (Yahoo Finance) so ranking works without Kite
- [x] Built-in 55-stock NSE universe, seeded on first start (Tata Motors → `TMPV` after demerger)
- [x] Live news from ET, ET Stocks, Livemint and Business Standard (Moneycontrol RSS is dead since 2024)
- [x] Keyword-accurate stock↔news matching and whole-word sentiment
- [x] NIFTY trend/regime, India VIX, global cues and NSE FII/DII in the market context
- [x] New technical score (trend/momentum/structure/volume/relative strength, with penalties)
- [x] Strategy detection: Breakout, Gap-up, Pullback to EMA, Trend Momentum, Earnings Momentum, Sector Rotation
- [x] Per-stock historical back-test of each setup, used as evidence and as a conviction adjustment
- [x] ATR-based trade plans: BUY/WAIT/AVOID, entry zone, stop, target, R:R, holding, risk, invalidation, exit logic
- [x] Position sizing from capital and risk per trade (Learning → Position sizing)
- [x] Fixed: `GET /recommendations` created a new DB row and LLM call on every request
- [x] Fixed: sequential LLM calls on every rank change (now deterministic reasons)
- [x] Fixed: entry/stop/target anchored to the 20-day low instead of the current price
- [x] Fixed: Analysis panel was empty for all but one stock; now PRD §9 sections for every stock
- [x] Fixed: chat had no memory; it now uses server-side history plus live ranking/trade context
- [x] Fixed: tradebook P&L ignored quantity; re-uploads duplicated rows; header rows in Console exports broke parsing
- [x] Fixed: support/resistance never reached the chart; stock `current_price` was never updated
- [x] Fixed: LLM outage made every call hang through three 60 s retries
- [x] News-triggered re-ranking (PRD §11) with UI toast
- [x] In-app 08:30 IST Morning Brief and 16:00 IST learning cycle (PRD §13, §18)
- [x] Auto-grading replays entry fill, stop and target on real bars (`not_triggered` handled)
- [x] Strategy Library performance (PRD §15) and a Learning panel with upload, results and trades
- [x] UI aligned to PRD §5: no mode tabs, names-only list, analysis bottom-left, chat bottom-right
- [x] Chart: EMA 20/50, volume, RSI and MACD bands, S/R and plan levels, trendline/level drawing (saved per stock), OHLC legend
- [x] Search that adds any NSE symbol to the watchlist (validated against market data)
- [x] Prepare order via Kite Publisher (user confirms on Zerodha), when `KITE_API_KEY` is set
- [x] Unit tests for the deterministic core (`backend/tests`)
- [x] Resizable panels: drag any divider; sizes saved per browser; Layout menu presets (Balanced/reset, Focus chat, Focus chart, Focus analysis)
- [x] Automatic / Manual list modes: Automatic = AI-ranked universe; Manual = your own list (add any NSE share by name or symbol, remove with ✕), same chart/plan/analysis/chat; search box in the list for both modes
- [x] Chat answers about the stock you have open: each question is tagged with it, its facts go first (in plain words), live facts sit after the history
- [x] Chart navigation: mouse wheel scrolls through time, Ctrl+wheel zooms, drag pans; ◀ ▶ − + Reset buttons; 1D–5Y/All range presets; opens on a readable recent window (daily = 6 months) and keeps your view on live refreshes
- [x] AI analyst learning loop (Qwen): reads every new headline (impact −2..+2, replaces keyword sentiment), forecasts top-10 + Manual list each weekday (5-day horizon), graded against real prices, reflects and rewrites versioned lessons fed into later prompts; earns ranking weight (up to ±5 pts) only after 30 graded forecasts that beat the naive baseline; track record + lessons in the Learning window; JSONL export for future fine-tuning
- [x] AI practice on historical charts whenever the market is closed (random stock + past date, chart only up to that day, date hidden, graded instantly; up to 400/day; pauses while you chat; separate track record, never earns ranking weight; triggers a lesson review every 20 cases)
- [x] AI next-session market outlook at 20:00 and 07:45 IST (headlines since close + global cues + FII/DII → NIFTY bias, probability, sector impacts); graded after the session; shown in the brief bar, brief, stock analysis (its sector) and chat
- [x] Probabilities clamped to 10–90% (a small model sometimes answers 0% or 100%)
- [x] Light theme with a dark/light toggle in the top bar (CSS-variable palette; chart re-themes too; choice saved per browser)
- [x] PROJECT_RULES.md, TASKS.md, ARCHITECTURE.md

## Open decisions (need the product owner)

- [ ] **Stack:** the PRD specifies WPF/.NET 9, but the build is FastAPI + React. Port, or amend the PRD?
- [ ] **Model:** move from Qwen 2.5 1.5B/Bionic to Qwen 3 on LM Studio (PRD). The 1.5B model still invents small details in chat.

## Next

- [ ] Instrument selection beyond cash equity (PRD §2/§11): ETFs, and futures/options using the
      Kite NFO option chain (needs a Kite subscription)
- [ ] Intraday breakout and volume-spike triggers from 5-minute bars during market hours (today:
      the scheduled cadence plus news triggers)
- [ ] Research Agent: corporate filings/announcements (NSE/BSE) as a news source
- [ ] Persist the Kite access token across backend restarts (it lives in memory until the daily expiry)
- [ ] Verify the Kite web deep link for "Open in Zerodha" (`dashboard#stocks/nse/<SYM>` is unverified)
- [ ] Holiday calendar for NSE, so the brief, outlook and learning jobs skip exchange holidays (practice already runs on holidays because the market is closed)
- [ ] Once the outlook has a graded record, let proven sector impacts nudge sentiment for stocks in those sectors (same earned-trust gate as forecasts)
- [ ] Frontend component tests; an end-to-end smoke test
- [ ] Fine-tune Qwen (LoRA) on the exported graded forecasts once there are several hundred and a GPU is available — then compare its hit rate against the prompt-only analyst before switching
- [ ] Surface `trading_memory` similar setups in the Analysis panel once real embeddings are available
