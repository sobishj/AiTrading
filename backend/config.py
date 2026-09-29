"""
Application configuration.

Loads settings from environment variables / .env file using pydantic-settings.
Import `settings` from this module anywhere config values are needed.
"""
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App ----
    APP_NAME: str = "AiTrading"
    APP_ENV: str = "development"
    SECRET_KEY: str = "change-this-in-production"

    # ---- Database ----
    DATABASE_URL: str = "postgresql://aitrading:aitrading@localhost:5432/aitrading"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_ECHO: bool = False

    # ---- Local LLM (Bionic / Qwen 2.5 1.5B, OpenAI-compatible) ----
    LLM_BASE_URL: str = "http://localhost:1234/v1"
    LLM_API_KEY: str = "not-needed"
    LLM_MODEL: str = "qwen2.5-1.5b-instruct"
    LLM_EMBEDDING_MODEL: str = "text-embedding-nomic-embed-text-v1.5"
    LLM_TIMEOUT: int = 60
    LLM_MAX_RETRIES: int = 3
    LLM_TEMPERATURE: float = 0.3

    # ---- Zerodha Kite Connect ----
    KITE_API_KEY: str = ""
    KITE_API_SECRET: str = ""
    KITE_ACCESS_TOKEN: str = ""
    KITE_REDIRECT_URL: str = "http://localhost:8000/kite/callback"

    # ---- CORS ----
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # ---- Frontend (used to redirect back after the Kite OAuth login flow) ----
    FRONTEND_URL: str = "http://localhost:5173"

    # ---- Logging ----
    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = "logs"

    # ---- Background job intervals (seconds) ----
    TECHNICAL_REFRESH_INTERVAL: int = 60
    RANKING_REFRESH_INTERVAL: int = 300
    MARKET_CONTEXT_REFRESH_INTERVAL: int = 900

    # ---- News ----
    # Legacy single-feed setting, still honored if set (added to NEWS_RSS_URLS).
    # Moneycontrol's RSS feeds stopped updating in April 2024, so it is no
    # longer part of the defaults.
    MONEYCONTROL_RSS_URL: str = ""
    NEWS_RSS_URLS: str = (
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms,"
        "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms,"
        "https://www.livemint.com/rss/markets,"
        "https://www.business-standard.com/rss/markets-106.rss"
    )
    NEWS_SCAN_INTERVAL: int = 180

    # ---- Market data ----
    # "auto" = Kite when connected, otherwise Yahoo Finance (free, no key).
    # "yahoo" / "kite" force one provider.
    MARKET_DATA_PROVIDER: str = "auto"
    YAHOO_CHART_URL: str = "https://query1.finance.yahoo.com/v8/finance/chart"
    # Daily history fetched per stock: enough for EMA-200, the 52-week high,
    # and the historical setup back-test in analysis_service.
    HISTORY_DAYS: int = 420

    # ---- Watchlist ----
    # Seed the stocks table with the built-in NSE large-cap universe
    # (universe.py) when it is empty on startup.
    SEED_WATCHLIST: bool = True

    # ---- Scheduled jobs (IST, HH:MM) ----
    MORNING_BRIEF_TIME: str = "08:30"
    # Qwen's daily forecasts (after the brief) — see ai_analyst_service.
    AI_FORECAST_TIME: str = "08:45"
    AI_ANALYST_ENABLED: bool = True
    # Next-session market outlook runs (IST, comma-separated): evening after the US open, and pre-market.
    AI_OUTLOOK_TIMES: str = "20:00,07:45"
    POST_MARKET_LEARNING_TIME: str = "16:00"

    # ---- Position sizing defaults (seed the app_settings row) ----
    DEFAULT_CAPITAL: float = 100000.0
    DEFAULT_RISK_PER_TRADE_PCT: float = 1.0

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def news_feed_list(self) -> List[str]:
        feeds = [url.strip() for url in self.NEWS_RSS_URLS.split(",") if url.strip()]
        if self.MONEYCONTROL_RSS_URL and self.MONEYCONTROL_RSS_URL not in feeds:
            feeds.append(self.MONEYCONTROL_RSS_URL)
        return feeds


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()


settings = get_settings()
