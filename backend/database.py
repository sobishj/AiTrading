"""
Database setup: SQLAlchemy engine, session factory, and pgvector extension bootstrap.
"""
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


engine = create_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,
    echo=settings.DB_ECHO,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    """Create the pgvector extension (if missing) and all ORM tables."""
    # Import models so they register on Base.metadata before create_all runs.
    import models  # noqa: F401

    with engine.connect() as conn:
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not ensure pgvector extension exists: %s", exc)
            conn.rollback()

    Base.metadata.create_all(bind=engine)

    # create_all() only creates missing tables, so on an already-existing
    # database newly added indexes (models.py) need to be created explicitly.
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_recommendations_stock_id ON recommendations (stock_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_trade_history_stock_id ON trade_history (stock_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_trade_history_recommendation_id ON trade_history (recommendation_id)"
        ))
        conn.commit()

    # Likewise create_all() never adds columns to existing tables. Additive,
    # idempotent migrations for columns introduced after the first release.
    with engine.connect() as conn:
        for statement in _COLUMN_MIGRATIONS:
            conn.execute(text(statement))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_recommendations_strategy ON recommendations (strategy)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_trade_history_trade_ref ON trade_history (trade_ref)"))
        conn.commit()

    if settings.SEED_WATCHLIST:
        seed_watchlist()

    logger.info("Database initialized (tables ensured, pgvector extension ensured, indexes ensured)")


_COLUMN_MIGRATIONS = [
    "ALTER TABLE stocks ADD COLUMN IF NOT EXISTS keywords TEXT",
    "ALTER TABLE stocks ADD COLUMN IF NOT EXISTS in_manual_list BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS action VARCHAR(10)",
    "ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS instrument VARCHAR(40)",
    "ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS strategy VARCHAR(40)",
    "ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS entry_low DECIMAL",
    "ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS entry_high DECIMAL",
    "ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS risk_reward DECIMAL",
    "ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS context_json TEXT",
    "ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS ai_commentary TEXT",
    "ALTER TABLE trade_history ADD COLUMN IF NOT EXISTS quantity DECIMAL",
    "ALTER TABLE trade_history ADD COLUMN IF NOT EXISTS exit_date DATE",
    "ALTER TABLE trade_history ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'auto'",
    "ALTER TABLE trade_history ADD COLUMN IF NOT EXISTS trade_ref VARCHAR(200)",
    "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS capital DECIMAL",
    "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS risk_per_trade_pct DECIMAL",
    "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS ai_practice_enabled BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE ai_predictions ADD COLUMN IF NOT EXISTS kind VARCHAR(10) NOT NULL DEFAULT 'live'",
    "CREATE INDEX IF NOT EXISTS ix_ai_predictions_kind ON ai_predictions (kind)",
]


def seed_watchlist() -> None:
    """Insert the built-in universe (universe.py) when no active stocks exist yet."""
    from models import Stock
    from universe import UNIVERSE

    with db_session() as db:
        if db.query(Stock).filter(Stock.watchlist_status == "active").count() > 0:
            return
        existing = {s.symbol: s for s in db.query(Stock).all()}
        for symbol, name, sector, keywords in UNIVERSE:
            stock = existing.get(symbol)
            if stock is None:
                db.add(Stock(symbol=symbol, name=name, sector=sector, instrument_type="equity",
                             watchlist_status="active", keywords=",".join(keywords)))
            else:
                stock.name, stock.sector, stock.keywords = name, sector, ",".join(keywords)
                stock.watchlist_status = "active"
        logger.info("Seeded watchlist with %d NSE stocks from universe.py", len(UNIVERSE))


def get_db() -> Generator:
    """FastAPI dependency yielding a scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session() -> Generator:
    """Context manager for use outside of FastAPI request handlers (e.g. background jobs)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
