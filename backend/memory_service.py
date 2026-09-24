"""
Trading memory and learning: stores trade predictions/outcomes with
embeddings, retrieves similar historical setups via pgvector cosine
similarity, and computes rolling prediction accuracy.
"""
import json
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from llm_service import llm_service
from models import TradeHistory, TradingMemory
from utils.logger import get_logger

logger = get_logger(__name__)


class MemoryService:
    async def store_trade_memory(self, db: Session, setup_description: str,
                                  outcome: str, accuracy_score: Optional[float] = None) -> TradingMemory:
        """Embed a trade setup description and persist it for future similarity search."""
        embedding = await llm_service.embed_text(setup_description)
        memory = TradingMemory(
            embedding=embedding,
            similar_past_setups=setup_description,
            outcome=outcome,
            accuracy_score=accuracy_score,
            timestamp=datetime.utcnow(),
        )
        db.add(memory)
        db.commit()
        db.refresh(memory)
        logger.info("Stored trading memory id=%s", memory.id)
        return memory

    async def find_similar_setups(self, db: Session, setup_description: str, limit: int = 5) -> list[dict]:
        """Retrieve the most semantically similar past trade setups via pgvector cosine distance."""
        query_embedding = await llm_service.embed_text(setup_description)

        stmt = (
            select(
                TradingMemory,
                TradingMemory.embedding.cosine_distance(query_embedding).label("distance"),
            )
            .order_by("distance")
            .limit(limit)
        )
        rows = db.execute(stmt).all()

        results = []
        for memory, distance in rows:
            results.append({
                "id": memory.id,
                "setup": memory.similar_past_setups,
                "outcome": memory.outcome,
                "accuracy_score": float(memory.accuracy_score) if memory.accuracy_score is not None else None,
                "similarity": round(1 - float(distance), 4) if distance is not None else None,
                "timestamp": memory.timestamp.isoformat(),
            })
        return results

    def calculate_accuracy(self, db: Session, days: int = 30) -> dict:
        """Compute prediction accuracy over the trailing `days` window from trade_history."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        rows = (
            db.query(TradeHistory)
            .filter(TradeHistory.timestamp >= cutoff, TradeHistory.actual_outcome.isnot(None))
            .all()
        )

        if not rows:
            return {"accuracy_pct": None, "sample_size": 0, "window_days": days}

        wins = sum(1 for r in rows if r.actual_outcome and r.actual_outcome.lower() in ("target_hit", "win", "profit"))
        accuracy_pct = round((wins / len(rows)) * 100, 2)

        return {"accuracy_pct": accuracy_pct, "sample_size": len(rows), "window_days": days}

    def get_recent_trade_history_rows(self, db: Session, limit: int = 10) -> list[dict]:
        """Rows formatted for PromptLibrary.format_few_shot_examples / coaching prompts."""
        rows = (
            db.query(TradeHistory)
            .order_by(TradeHistory.timestamp.desc())
            .limit(limit)
            .all()
        )
        out = []
        for r in rows:
            out.append({
                "symbol": r.stock.symbol if r.stock else f"stock_{r.stock_id}",
                "entry_price": float(r.entry_price) if r.entry_price is not None else None,
                "predicted_target": float(r.predicted_target) if r.predicted_target is not None else None,
                "actual_outcome": r.actual_outcome or "pending",
                "profit_loss": float(r.profit_loss) if r.profit_loss is not None else None,
            })
        return out


memory_service = MemoryService()
