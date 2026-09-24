"""
Standalone digest job — optional since v2.

The API server now runs the same work on its own schedule (see main.py:
the learning cycle at POST_MARKET_LEARNING_TIME and the Morning Brief at
MORNING_BRIEF_TIME, IST). Use this script only when the server isn't
running around those times, e.g. via Windows Task Scheduler (README §6).

What it does, in order:
1. Grades matured recommendations against the price action after they were
   issued (entry filled? stop or target first? expired?) into trade_history.
2. Recalibrates the ranking weights from the graded outcomes.
3. Re-ranks the watchlist and writes today's Morning Brief, recording the top
   BUY ideas as recommendations.

It never touches the LLM's weights: "learning" means the retrieval corpus,
strategy statistics and score-blend weights adapt to real outcomes.
"""
import asyncio
import os
import sys
from datetime import datetime

# Allow running as `python scripts/nightly_digest.py` from anywhere — Task
# Scheduler invokes this with a bare interpreter path and no env setup.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # so .env and logs/ resolve

from brief_service import brief_service  # noqa: E402
from data_provider import yahoo_provider  # noqa: E402
from database import db_session, init_db  # noqa: E402
from learning_service import learning_service  # noqa: E402
from utils.logger import get_logger  # noqa: E402

logger = get_logger(__name__)


async def run_nightly_digest() -> None:
    started = datetime.utcnow()
    logger.info("=== Digest starting ===")
    init_db()

    with db_session() as db:
        cycle = await learning_service.run_learning_cycle(db)
        logger.info("Graded %d recommendation(s); recalibration: %s", cycle["graded"], cycle["recalibration"])

        brief = await brief_service.generate(db, force=True)
        logger.info("Morning brief:\n%s", brief.brief_text)

    await yahoo_provider.close()
    logger.info("=== Digest complete in %.1fs ===", (datetime.utcnow() - started).total_seconds())


if __name__ == "__main__":
    asyncio.run(run_nightly_digest())
