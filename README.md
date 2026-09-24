# TradeAI

AI-powered NSE market analyst. Continuously ranks a watchlist of NSE stocks by
conviction, detects the setup behind each one (breakout, pullback, momentum,
gap-up, earnings momentum, sector rotation), back-tests that setup on the stock's
own history, and turns the best ones into trade plans with entry, stop-loss,
target, risk/reward and position size. News, NIFTY regime, India VIX, global cues
and FII/DII flows feed in live; a ChatGPT-style coach explains any of it.

Runs locally: **FastAPI** backend + **React/TypeScript** frontend + **PostgreSQL**,
with any OpenAI-compatible local LLM (LM Studio / Bionic) on `localhost:1234`.
Market data comes from **Zerodha Kite** when you're logged in and from **Yahoo
Finance** otherwise, so it works with no broker account at all.

See `ARCHITECTURE.md` for how it works, `TASKS.md` for status, and
`PROJECT_RULES.md` before changing anything.

---

## Quick start (Windows)

Double-click **`start-tradeai.bat`** in the project folder. It starts Docker/PostgreSQL,
Bionic (if installed), the backend (port 8000, or 8010 if 8000 is taken) and the
frontend, then opens <http://localhost:5173> in Chrome. Running it again is safe —
anything already running is left alone. **`stop-tradeai.bat`** stops the backend and
frontend (Docker and Bionic keep running). The sections below cover first-time setup.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.14 (3.10+ works) | The pinned stack is verified on 3.14 |
| Node.js | 18+ | Verified on v22 |
| PostgreSQL | 15+ | **plus the `pgvector` extension** — see below |
| LM Studio or Bionic | any | Optional. Serves an OpenAI-compatible API on `localhost:1234`. Rankings and plans work without it; chat and AI notes need it |

> **Note on technical indicators:** this project uses [`ta`](https://pypi.org/project/ta/)
> rather than `pandas-ta`. `pandas-ta` depends on `numba`, which does not support
> Python 3.14. `ta` is pure-Python and computes the same RSI/MACD/EMA values
> (verified to match hand-computed pandas references to within 0.01).

---

## 1. Database setup

### Quickest: Docker

`docker compose up -d` from the repo root starts PostgreSQL 17 + pgvector on
port **5433** (user/password/db `tradeai`) plus pgAdmin on 5050. Point
`DATABASE_URL` at `postgresql://tradeai:tradeai@localhost:5433/tradeai` and skip
the rest of this section. The backend creates and migrates tables on startup.

### Install pgvector (native PostgreSQL)

The `trading_memory` table stores embeddings in a `vector(1536)` column, so the
`pgvector` extension is **required**. It does not ship with PostgreSQL.

On Windows, download the release matching your PostgreSQL major version from
<https://github.com/pgvector/pgvector/releases> and copy the files into your
PostgreSQL install (default `C:\Program Files\PostgreSQL\17\`):

```
vector.dll          ->  <PG>\lib\
vector.control      ->  <PG>\share\extension\
vector--*.sql       ->  <PG>\share\extension\
```

Verify it registered:

```bash
psql -U postgres -c "SELECT * FROM pg_available_extensions WHERE name = 'vector';"
```

### Create the role and database

```bash
psql -U postgres
```
```sql
CREATE ROLE tradeai WITH LOGIN PASSWORD 'tradeai';
CREATE DATABASE tradeai OWNER tradeai;
```

### Apply the schema

```bash
psql -U tradeai -d tradeai -f config/database.sql
```

This creates every table, enables the `vector` extension, and builds the
`ivfflat` cosine index used for semantic search over past trade setups.
See `config/setup.md` for more detail.

---

## 2. Backend setup

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
copy .env.example .env         # cp on macOS/Linux
```

Edit `.env` — at minimum confirm `DATABASE_URL` matches the role/password you
created above. Key settings:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql://tradeai:tradeai@localhost:5432/tradeai` | Postgres connection |
| `LLM_BASE_URL` | `http://localhost:1234/v1` | Bionic OpenAI-compatible endpoint |
| `LLM_MODEL` | `qwen2.5-1.5b-instruct` | Chat model id |
| `LLM_EMBEDDING_MODEL` | `text-embedding-nomic-embed-text-v1.5` | Embedding model id |
| `KITE_API_KEY` / `KITE_API_SECRET` | *(empty)* | Zerodha — see below |
| `RANKING_REFRESH_INTERVAL` | `300` | **One-time seed only** — see "Configurable auto-refresh" below |

Run it:

```bash
python -m uvicorn main:app --reload --port 8000
```

Interactive API docs: <http://localhost:8000/docs>
Health check: <http://localhost:8000/health>

---

## 3. Frontend setup

```bash
cd frontend
npm install
copy .env.example .env
npm run dev
```

Opens on <http://localhost:5173>. Vite proxies `/api` to `localhost:8000`, so the
defaults work without changes.

---

## 4. Market data and Zerodha

**No setup needed for data:** without Kite, candles come from Yahoo Finance's
public chart API (NSE prices, delayed up to ~15 min), which is enough for the
swing-trade horizon the engine targets. Set `MARKET_DATA_PROVIDER=kite` or `yahoo`
to force one source.

Kite Connect is **optional**. With it you get broker data and **Prepare order**
(Kite Publisher): TradeAI builds a LIMIT order basket and Zerodha's own
window opens for you to review and confirm.

1. Create an app at <https://developers.kite.trade/apps> (₹2000/month subscription).
2. Set the redirect URL to `http://localhost:8000/kite/callback`.
3. Put the API key and secret in `backend/.env`.
4. Access tokens expire daily. Open <http://localhost:8000/kite/login> once per
   trading day; the callback stores the session in memory.

**TradeAI never places orders.** Every order is confirmed by you on Zerodha.

---

## 5. Configurable auto-refresh

The background loop that re-ranks the watchlist and pushes updates over
WebSocket runs on a cadence you control at runtime — no restart needed:

- **UI**: the "Auto-refresh" dropdown in the top bar (1 / 2 / 5 / 10 / 30 min,
  or **Never**). Changing it calls `PUT /api/settings/refresh-interval`
  immediately.
- **API**: `PUT /api/settings/refresh-interval` with `{"seconds": 120}`, or
  `{"seconds": null}` to disable auto-refresh entirely. `GET /api/settings`
  reads the current value.

The setting is persisted in the `app_settings` table (not `.env`), so it
survives restarts. `RANKING_REFRESH_INTERVAL` in `.env` only seeds that row
the very first time the table is created — after that, editing `.env` has no
effect; use the API/UI instead. Minimum is 30 seconds, to avoid hammering the
local LLM with rapid re-analysis.

With auto-refresh set to **Never**, ranking only updates when you hit
`POST /api/refresh` manually or when the nightly digest job runs.

---

## 6. Continuous engine, morning brief & learning

The backend runs these on its own while it's up (`main.py`):

| Job | When | What |
|---|---|---|
| Ranking | Every auto-refresh interval (§5) | Re-rank the watchlist, store the top 5 BUY ideas as recommendations, push the new order over WebSocket |
| News watch | Every 3 min | New headline mentioning a watchlist stock → immediate re-rank + UI toast |
| Morning brief | 08:30 IST, weekdays | Today's best trade + market context (also generated on first request of the day) |
| Learning | 16:00 IST, weekdays | Grade matured recommendations against real prices; recalibrate weights |

If the server isn't running at those times, `python backend/scripts/nightly_digest.py`
does the learning + brief in one go (e.g. via Task Scheduler at 08:15):

```powershell
schtasks /create /tn "TradeAI Digest" ^
  /tr "C:\VSCode\TradeAI\backend\venv\Scripts\python.exe C:\VSCode\TradeAI\backend\scripts\nightly_digest.py" ^
  /sc weekly /d MON,TUE,WED,THU,FRI /st 08:15
```

### Uploading Zerodha trades

Top bar → **Learning** → choose the tradebook (Console → Reports → Tradebook,
CSV or XLSX). Executions are FIFO-matched into round trips (P&L × quantity),
de-duplicated by trade ID (so re-uploading is safe), and linked to the TradeAI
recommendation issued in the 10 days before each entry. A learning cycle runs right
after the import.

### What actually "learns" — read this before trusting it

**The LLM never learns or retrains.** What adapts is inspectable:

- **Strategy statistics:** win rate and average return per strategy, from graded
  recommendations and your real trades. After 8+ trades a strategy's record shifts
  conviction for new setups of that type by at most ±5 points.
- **Per-stock back-test:** every ranking run replays each setup on the stock's last
  ~year of daily bars (1.5-ATR stop, 3-ATR target, 10 bars). Its win rate adjusts
  conviction by at most ±5 points and is shown in the Analysis panel.
- **Weight recalibration:** the technical/news/volume blend (default 55/30/15)
  moves toward whichever component separated winners from losers. It needs 15+
  graded trades, blends 70/30 with the old weights, and clamps each weight to
  [0.10, 0.70].
- **Retrieval:** graded setups are embedded into `trading_memory` (pgvector).
- **AI analyst (Qwen) track record:** Qwen reads every new headline about your
  stocks, forecasts the top 10 and your Manual list each weekday morning, and is
  graded 5 trading days later. After grading it reviews its hits and misses and
  rewrites a versioned list of lessons that feeds every later forecast, analysis
  note and chat. Its forecasts affect the ranking only after 30 are graded and it
  beats the naive "always up/down" baseline, with a weight that grows with its edge
  (max ±5 points). Everything is visible under **Learning → AI analyst (Qwen) track
  record**, and graded forecasts can be exported as JSONL for a future fine-tune.
- **Practice while the market is closed:** evenings, nights and weekends, Qwen
  replays historical charts (a random stock at a random past date, seeing only the
  bars up to that day, date hidden) and is graded instantly — hundreds of cases a
  day that feed its lessons. Practice has its own track record, pauses while you
  chat, can be turned off in the Learning window, and never earns ranking weight.
- **Next-session outlook:** at 20:00 and 07:45 IST Qwen reads the headlines since
  the close, global markets, crude, USD/INR and FII/DII flows, and forecasts the
  next NIFTY session plus the sectors the news affects. It shows as the "Next
  session" pill in the brief bar, in each stock's analysis (for its sector) and in
  chat, and is graded after the session.

None of this is a substitute for your own judgment.

---

## 7. First run walkthrough

1. **Start PostgreSQL** (`docker compose up -d`).
2. **Start the backend** (`python -m uvicorn main:app --port 8000` in `backend/`).
   On first start it creates tables and seeds a 55-stock NSE large-cap watchlist
   (`backend/universe.py`), then ranks it within seconds.
3. **Optional:** start LM Studio (or Bionic) with your model on port 1234 for chat
   and AI notes. The top bar shows *AI online/offline*.
4. **Start the frontend** (`npm run dev` in `frontend/`) and open <http://localhost:5173>.
5. The left panel lists stock names, strongest opportunity first. The app opens on
   today's best trade: chart with entry/stop/target lines on it, trade plan on the
   right, and why the stock ranks there bottom-left.
6. Switch the left panel to **Manual** to build your own list: type a company name or NSE symbol in its
   search box to add a share, and hover over a name and click ✕ to remove it. **Automatic** is the AI-ranked universe.
   Both lists have a search box.
7. Ask the coach (bottom-right), e.g. "Why did BEL become number one?" or
   "Compare Tata Motors with Reliance". It remembers the conversation.
8. Set your capital and risk per trade under **Learning** so plans show position
   sizes, and upload your tradebook there as you trade.

---

## Architecture

```
TradeAI/
├── backend/
│   ├── main.py              FastAPI app, Kite OAuth, background engine (ranking, news trigger, scheduler)
│   ├── config.py            pydantic-settings configuration
│   ├── database.py          Engine, sessions, pgvector bootstrap, additive migrations, watchlist seeding
│   ├── models.py / schemas.py  ORM models / API schemas
│   ├── universe.py          Built-in NSE large-cap watchlist (names, sectors, news keywords)
│   ├── data_provider.py     Yahoo Finance candles (fallback when Kite isn't connected)
│   ├── market_service.py    Candle source, RSS news + sentiment, NIFTY regime, VIX, global cues, FII/DII
│   ├── analysis_service.py  Indicators, technical score, setup detection, back-test, trade plans
│   ├── ranking_service.py   Conviction, strategies, evidence adjustments, ranking, recommendations, sizing
│   ├── learning_service.py  Auto-grading, tradebook import, strategy performance, learning cycle
│   ├── brief_service.py     Morning brief
│   ├── insight_service.py   Analysis-panel sections, LLM narrative, chat grounding
│   ├── llm_service.py       Semantic Kernel + availability probe + background LLM queue
│   ├── memory_service.py    Embeddings + pgvector similarity over past setups
│   ├── kite_service.py      Zerodha auth, historical data, order baskets, tradebook parsing
│   ├── api/routes.py        All endpoints + WebSocket
│   ├── scripts/nightly_digest.py  Standalone learning + brief (optional, §6)
│   ├── prompts/             All prompt templates (PromptLibrary)
│   ├── tests/               Unit tests for the deterministic core
│   └── utils/               Logging, retry/rate-limit decorators, validators
├── frontend/
│   └── src/
│       ├── services/        API client, WebSocket client, shared types
│       ├── hooks/           useStocks, useResource, useChat, useSettings, useWebSocket
│       ├── layouts/         PRD §5 MainLayout
│       └── components/      Navigation, StockList, BestPick (brief), Chart, TradePlan, Analysis, Chat, Learning
├── config/
│   ├── database.sql         Full schema
│   └── setup.md             Database setup detail
├── ARCHITECTURE.md · TASKS.md · PROJECT_RULES.md
```

Frontend follows MVVM: components render, hooks hold view state, services own
I/O. Prompts live only in `prompts/prompt_library.py`, never inline in service code.
See `ARCHITECTURE.md` for the full design.

### Scoring model

Full details are in `ARCHITECTURE.md`. In short, a 0–100 **technical score**
(trend 30, momentum 25, structure 20, volume 15, relative strength vs NIFTY 10,
minus overbought/extension/volatility penalties) is blended with **news sentiment**
and **volume confirmation** using the adaptive weights (55/30/15 by default). Then
bounded evidence adjustments are added: per-stock back-test edge, learned strategy
edge, sector-rotation and earnings-momentum bonuses. Each stock gets **BUY / WAIT /
AVOID** and an ATR-based plan (stop ≤ 1.5 ATR, 2R target).

---

## API reference

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/api/status` | Data source, LLM availability, last ranking time, market regime |
| `GET` | `/api/stocks` | Watchlist in live-ranking order |
| `GET` | `/api/stocks?list=auto\|manual\|all` | Automatic universe, your Manual list, or both, in ranking order |
| `POST` / `DELETE` | `/api/watchlist/manual`, `/api/watchlist/manual/{symbol}` | Add (validated) / remove a share on your Manual list |
| `POST` / `DELETE` | `/api/stocks`, `/api/stocks/{symbol}` | Add / remove a stock in the Automatic universe |
| `GET` | `/api/stocks/{symbol}` | Detail + technical snapshot |
| `GET` | `/api/stocks/{symbol}/chart` | Candles + EMA/RSI/MACD overlays + S/R and plan levels |
| `GET` | `/api/stocks/{symbol}/candles` | Raw OHLCV |
| `GET` | `/api/stocks/{symbol}/trade-plan` | Trade plan (BUY plans are persisted as recommendations) |
| `GET` | `/api/stocks/{symbol}/analysis` | Why it ranks here: context, confirmation, news, risks, history, exit |
| `GET` | `/api/stocks/{symbol}/analysis/narrative` | Optional LLM analyst note |
| `GET` | `/api/stocks/{symbol}/order-basket` | Kite Publisher basket (user confirms on Zerodha) |
| `GET` | `/api/recommendations` | Today's best actionable pick |
| `GET` | `/api/recommendations/history` | Market memory: past calls and outcomes |
| `GET` / `POST` | `/api/morning-brief`, `/api/morning-brief/regenerate` | Today's brief |
| `POST` | `/api/chat` | Coach chat (grounded in live ranking + history) |
| `GET` / `DELETE` | `/api/chat/history` | Conversation history |
| `POST` | `/api/upload-trades` | Upload a Zerodha tradebook (CSV/XLSX) |
| `GET` | `/api/trade-history` | Graded calls and imported trades (`?source=auto` or `zerodha`) |
| `GET` | `/api/strategies` | Strategy Library performance |
| `POST` | `/api/learning/run` | Grade + recalibrate now |
| `GET` | `/api/market-context` | NIFTY regime, VIX, global cues, FII/DII, headlines |
| `POST` | `/api/refresh` | Force a re-rank and broadcast |
| `GET` | `/api/settings` | Refresh interval, adaptive weights, position sizing |
| `PUT` | `/api/settings/refresh-interval` | `{"seconds": 120}` or `{"seconds": null}` (never) |
| `PUT` | `/api/settings/risk` | `{"capital": 100000, "risk_per_trade_pct": 1}` |
| `WS` | `/api/ws/updates` | `ranking_update`, `morning_brief`, `learning_update` events |

---

## Troubleshooting

**`password authentication failed for user "tradeai"`**
The role doesn't exist yet or the password differs. See step 1.

**`extension "vector" is not available`**
pgvector isn't installed into your PostgreSQL. See step 1.

**Chat replies "I can't reach the local model"**
LM Studio/Bionic isn't running or isn't on port 1234. Check `curl http://localhost:1234/v1/models`
and confirm `LLM_MODEL` matches an id from that response.

**Charts are empty / "No candle data"**
Yahoo Finance may be unreachable from your network, or the symbol isn't listed on
NSE. Check `backend/logs/tradeai.log` for `Yahoo chart ... returned HTTP`.

**Running the tests**
`cd backend && venv\Scripts\python -m pip install -r requirements-dev.txt && venv\Scripts\python -m pytest -q`,
then `cd frontend && npx tsc -b`.

**`Cannot install on Python version 3.14` when installing**
You're installing `pandas-ta` from an older requirements file. This project uses
`ta` instead — reinstall from the current `backend/requirements.txt`.
