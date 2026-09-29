-- AiTrading PostgreSQL schema
-- Run against an empty database: psql -U aitrading -d aitrading -f config/database.sql

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS stocks (
  id SERIAL PRIMARY KEY,
  symbol VARCHAR(20) UNIQUE NOT NULL,
  name VARCHAR(100) NOT NULL,
  sector VARCHAR(50),
  instrument_type VARCHAR(20) DEFAULT 'equity',
  current_price DECIMAL,
  watchlist_status VARCHAR(20) DEFAULT 'active',
  keywords TEXT,  -- comma-separated news keywords (see backend/universe.py)
  in_manual_list BOOLEAN NOT NULL DEFAULT FALSE,  -- user's Manual list (Auto universe = watchlist_status 'active')
  last_updated TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS recommendations (
  id SERIAL PRIMARY KEY,
  stock_id INTEGER REFERENCES stocks(id) ON DELETE CASCADE,
  entry_price DECIMAL NOT NULL,
  target_price DECIMAL NOT NULL,
  stop_loss DECIMAL NOT NULL,
  holding_period VARCHAR(50) NOT NULL,
  risk_level VARCHAR(20) NOT NULL,
  reasoning TEXT NOT NULL,
  timestamp TIMESTAMP DEFAULT NOW(),
  confidence_score DECIMAL DEFAULT 0,
  -- Component scores at time of recommendation, used by the nightly digest
  -- to recalibrate ranking weights against real outcomes.
  technical_score DECIMAL,
  sentiment_score DECIMAL,
  volume_score DECIMAL,
  -- v2 trade plan + market memory
  action VARCHAR(10),
  instrument VARCHAR(40),
  strategy VARCHAR(40),
  entry_low DECIMAL,
  entry_high DECIMAL,
  risk_reward DECIMAL,
  context_json TEXT,
  ai_commentary TEXT
);

CREATE TABLE IF NOT EXISTS trade_history (
  id SERIAL PRIMARY KEY,
  stock_id INTEGER REFERENCES stocks(id) ON DELETE CASCADE,
  recommendation_id INTEGER REFERENCES recommendations(id) ON DELETE SET NULL,
  execution_date DATE NOT NULL,
  entry_price DECIMAL NOT NULL,
  exit_price DECIMAL,
  profit_loss DECIMAL,
  predicted_target DECIMAL,
  actual_outcome VARCHAR(50),
  timestamp TIMESTAMP DEFAULT NOW(),
  quantity DECIMAL,
  exit_date DATE,
  source VARCHAR(20) DEFAULT 'auto',  -- 'auto' (graded call) | 'zerodha' (imported trade)
  trade_ref VARCHAR(200)              -- dedupe key for imported trades
);

-- Singleton row (id = 1) holding user-configurable settings: auto-refresh
-- cadence (NULL = disabled/"never") and the adaptive ranking weights.
CREATE TABLE IF NOT EXISTS app_settings (
  id INTEGER PRIMARY KEY DEFAULT 1,
  ranking_refresh_seconds INTEGER,
  weight_technical DECIMAL DEFAULT 0.55,
  weight_sentiment DECIMAL DEFAULT 0.30,
  weight_volume DECIMAL DEFAULT 0.15,
  capital DECIMAL,
  risk_per_trade_pct DECIMAL,
  updated_at TIMESTAMP DEFAULT NOW(),
  CONSTRAINT app_settings_singleton CHECK (id = 1)
);

CREATE TABLE IF NOT EXISTS user_chats (
  id SERIAL PRIMARY KEY,
  user_message TEXT NOT NULL,
  ai_response TEXT NOT NULL,
  stock_context VARCHAR(20),
  timestamp TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS technical_indicators (
  id SERIAL PRIMARY KEY,
  stock_id INTEGER REFERENCES stocks(id) ON DELETE CASCADE,
  rsi DECIMAL,
  macd DECIMAL,
  volume BIGINT,
  ema_20 DECIMAL,
  ema_50 DECIMAL,
  timestamp TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS market_snapshots (
  id SERIAL PRIMARY KEY,
  market_date DATE DEFAULT CURRENT_DATE,
  fii_activity DECIMAL,
  dii_activity DECIMAL,
  news_sentiment TEXT,
  global_market_summary TEXT,
  context_json TEXT,
  timestamp TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trading_memory (
  id SERIAL PRIMARY KEY,
  embedding vector(1536),
  similar_past_setups TEXT,
  outcome TEXT,
  accuracy_score DECIMAL,
  timestamp TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS morning_briefs (
  id SERIAL PRIMARY KEY,
  brief_date DATE UNIQUE NOT NULL,
  recommendation_id INTEGER REFERENCES recommendations(id) ON DELETE SET NULL,
  market_summary TEXT,
  brief_text TEXT NOT NULL,
  context_json TEXT,
  timestamp TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recommendations_strategy ON recommendations(strategy);
CREATE INDEX IF NOT EXISTS idx_trade_history_trade_ref ON trade_history(trade_ref);
CREATE INDEX IF NOT EXISTS idx_stocks_symbol ON stocks(symbol);
CREATE INDEX IF NOT EXISTS idx_recommendations_timestamp ON recommendations(timestamp);
CREATE INDEX IF NOT EXISTS idx_trade_history_stock ON trade_history(stock_id);
CREATE INDEX IF NOT EXISTS idx_trade_history_recommendation ON trade_history(recommendation_id);
CREATE INDEX IF NOT EXISTS idx_technical_indicators_stock ON technical_indicators(stock_id);
CREATE INDEX IF NOT EXISTS idx_trading_memory_embedding
  ON trading_memory USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);
