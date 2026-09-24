"""
Input validation helpers shared across API routes and services.
"""
import re

from fastapi import HTTPException

_SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")

REQUIRED_TRADE_CSV_COLUMNS = {
    "symbol", "trade_date", "trade_type", "quantity", "price",
}


def validate_symbol(symbol: str) -> str:
    """Normalize and validate an NSE stock/derivative symbol."""
    normalized = symbol.strip().upper()
    if not _SYMBOL_RE.match(normalized):
        raise HTTPException(status_code=422, detail=f"Invalid stock symbol: {symbol!r}")
    return normalized


def validate_chat_message(message: str, max_length: int = 2000) -> str:
    stripped = message.strip()
    if not stripped:
        raise HTTPException(status_code=422, detail="Chat message cannot be empty")
    if len(stripped) > max_length:
        raise HTTPException(status_code=422, detail=f"Chat message exceeds {max_length} characters")
    return stripped


def validate_trade_csv_columns(columns: set[str]) -> None:
    lowered = {c.strip().lower() for c in columns}
    missing = REQUIRED_TRADE_CSV_COLUMNS - lowered
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Uploaded trade report is missing required columns: {sorted(missing)}",
        )
