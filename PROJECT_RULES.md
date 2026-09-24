# TradeAI — Project Rules

Rules for anyone (human or AI) changing this codebase. They come from PRD v2.0 §21 plus
conventions established while building v2.

## Product rules

1. **Do not redesign the approved UI.** The layout is fixed by PRD §5: top bar with search;
   left = stock names only; center = chart; right = trade plan; bottom-left = AI analysis;
   bottom-right = AI chat. Improve panels in place; don't move, merge or tab them.
2. **The user never picks a trading mode.** No Swing/Intraday/Options tabs. The engine picks the
   setup and instrument.
3. **The left panel shows names only.** No scores, badges or prices in the list. A brief highlight
   on rank changes is the only allowed cue.
4. **Every recommendation carries reasoning, entry, stop-loss, target and risk.** No plan without
   all five.
5. **The user always has final control over orders.** TradeAI never calls `place_order`. "Prepare
   order" hands a basket to Zerodha's own confirmation screen (Kite Publisher).
6. **Learn from evidence, never by rewriting itself.** Learning means graded outcomes, strategy
   statistics, bounded weight adjustments and the AI analyst's graded forecasts and written
   lessons — all inspectable in the Learning panel. Nothing fine-tunes the LLM in-app.

## Engineering rules

7. **Build incrementally; don't rewrite completed modules.** Extend with new fields that have
   defaults (see `TechnicalSnapshot`) and additive DB migrations (`database._COLUMN_MIGRATIONS`).
8. **All prompts live in `backend/prompts/prompt_library.py`.** No prompt text inline in services.
9. **The ranking engine must never depend on the LLM.** Scores, setups, plans and analysis sections
   are deterministic (`analysis_service`, `ranking_service`, `insight_service`). The LLM only adds
   narrative, through `llm_service`, which returns fallbacks immediately when the model is offline.
10. **The LLM influences ranking only through earned, precomputed inputs** (AI news reads and
    forecasts produced in the background, weighted by the graded track record). **No LLM calls on
    the ranking hot path.** Rank-change reasons are built deterministically.
    Background LLM work goes through `llm_service.enqueue` (one job at a time).
11. **Market data goes through `market_service.get_candles_for_symbol`.** It chooses Kite or Yahoo;
    don't call a provider directly from features.
12. **Recommendations are market memory.** Only actionable (BUY) plans are persisted, and a
    still-valid recommendation is reused rather than duplicated (`RankingService._still_valid`).
13. **Tradebook imports must be idempotent.** Every imported row has a `trade_ref`.
14. **Times:** DB timestamps are naive UTC; market logic uses IST (`Asia/Kolkata`).
15. **Tests:** deterministic logic gets unit tests in `backend/tests/` (no network, no DB). Run
    `venv\Scripts\python -m pytest -q` from `backend/` and `npx tsc -b` from `frontend/` before
    handing work over.
16. **Keep `TASKS.md` and `ARCHITECTURE.md` current** whenever a feature lands or a design decision
    changes.
