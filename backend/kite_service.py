"""
Zerodha Kite Connect integration.

Handles authentication, fetching holdings/positions/margins, preparing
(but never auto-executing) order data, generating manual-execution trade
URLs, and parsing uploaded Zerodha tradebook CSV/XLSX reports.

Orders are intentionally never placed automatically — TradeAI is an
advisory tool, not an auto-trading bot.
"""
import csv
import io
from datetime import datetime
from typing import Optional

import pandas as pd
from kiteconnect import KiteConnect
from kiteconnect.exceptions import KiteException

from config import settings
from utils.logger import get_logger
from utils.validators import validate_trade_csv_columns

logger = get_logger(__name__)

# .env.example ships these as visible placeholders so users know the field
# exists. If they're still sitting in .env verbatim, that's not a real key.
_PLACEHOLDER_VALUES = {"your_kite_api_key", "your_kite_api_secret", ""}


class KiteService:
    def __init__(self) -> None:
        self._api_key = settings.KITE_API_KEY
        self._access_token = settings.KITE_ACCESS_TOKEN
        self.kite: Optional[KiteConnect] = None

        if self._api_key and self._api_key not in _PLACEHOLDER_VALUES:
            self.kite = KiteConnect(api_key=self._api_key)
            if self._access_token and self._access_token not in _PLACEHOLDER_VALUES:
                self.kite.set_access_token(self._access_token)
        else:
            logger.warning("KITE_API_KEY not configured (or still a placeholder) — Kite integration disabled")

    @property
    def api_key(self) -> Optional[str]:
        """Public app key (needed by the Kite Publisher order basket; not a secret)."""
        return self._api_key if self.kite is not None else None

    @property
    def has_api_key(self) -> bool:
        """API key/secret present, but the user may not have completed login yet."""
        return self.kite is not None

    @property
    def is_configured(self) -> bool:
        """API key present AND a valid access token — i.e. actually ready to fetch data."""
        return self.kite is not None and bool(self._access_token)

    def get_login_url(self) -> Optional[str]:
        if not self.kite:
            return None
        return self.kite.login_url()

    def generate_session(self, request_token: str) -> dict:
        """Exchange a request_token (from the Kite login redirect) for an access_token."""
        if not self.kite:
            raise RuntimeError("Kite API key not configured")
        try:
            session_data = self.kite.generate_session(request_token, api_secret=settings.KITE_API_SECRET)
            self._access_token = session_data["access_token"]
            self.kite.set_access_token(self._access_token)
            logger.info("Kite session established")
            return session_data
        except KiteException as exc:
            logger.error("Kite session generation failed: %s", exc)
            raise

    def get_holdings(self) -> list[dict]:
        if not self.is_configured:
            return []
        try:
            return self.kite.holdings()
        except KiteException as exc:
            logger.error("Failed to fetch holdings: %s", exc)
            return []

    def get_positions(self) -> dict:
        if not self.is_configured:
            return {"net": [], "day": []}
        try:
            return self.kite.positions()
        except KiteException as exc:
            logger.error("Failed to fetch positions: %s", exc)
            return {"net": [], "day": []}

    def get_margins(self) -> dict:
        if not self.is_configured:
            return {}
        try:
            return self.kite.margins()
        except KiteException as exc:
            logger.error("Failed to fetch margins: %s", exc)
            return {}

    def get_historical_data(self, instrument_token: int, from_date: datetime, to_date: datetime,
                             interval: str = "day") -> list[dict]:
        if not self.is_configured:
            return []
        try:
            return self.kite.historical_data(instrument_token, from_date, to_date, interval)
        except KiteException as exc:
            logger.error("Failed to fetch historical data: %s", exc)
            return []

    @staticmethod
    def prepare_order_data(symbol: str, transaction_type: str, quantity: int,
                            order_type: str = "MARKET", price: Optional[float] = None,
                            product: str = "CNC") -> dict:
        """
        Build an order payload dict for manual review — TradeAI never calls
        kite.place_order() itself. The frontend surfaces this via an
        "Open in Zerodha" deep link instead.
        """
        return {
            "tradingsymbol": symbol,
            "exchange": "NSE",
            "transaction_type": transaction_type.upper(),
            "quantity": quantity,
            "order_type": order_type.upper(),
            "price": price,
            "product": product,
            "note": "Prepared by TradeAI — review before executing manually in Kite.",
        }

    @staticmethod
    def generate_trade_url(symbol: str) -> str:
        """Deep link into Kite's web trading terminal for manual order placement."""
        return f"https://kite.zerodha.com/dashboard#stocks/nse/{symbol.upper()}"

    @staticmethod
    def parse_trade_report(file_bytes: bytes, filename: str) -> pd.DataFrame:
        """
        Parse an uploaded Zerodha tradebook (CSV or XLSX) into a normalized
        DataFrame. Console's XLSX export puts a block of account details above
        the real header row, so the header is located by content rather than
        assumed to be the first row.
        """
        is_excel = filename.lower().endswith((".xlsx", ".xls"))
        if is_excel:
            raw = pd.read_excel(io.BytesIO(file_bytes), header=None, dtype=str)
        else:
            # csv.reader copes with ragged rows (short metadata lines above a
            # wide header), which pandas would size from the first line.
            rows = list(csv.reader(io.StringIO(file_bytes.decode("utf-8-sig", errors="replace"))))
            raw = pd.DataFrame(rows, dtype=str)

        def normalize(value) -> str:
            return str(value).strip().lower().replace(" ", "_") if pd.notna(value) else ""

        header_idx = 0
        for idx in range(min(len(raw), 40)):
            cells = {normalize(v) for v in raw.iloc[idx].tolist()}
            if {"symbol", "trade_type"} <= cells:
                header_idx = idx
                break

        df = raw.iloc[header_idx + 1:].copy()
        df.columns = [normalize(c) or f"col_{i}" for i, c in enumerate(raw.iloc[header_idx].tolist())]
        df = df.loc[:, ~df.columns.str.startswith("col_")]
        df = df.dropna(how="all")
        validate_trade_csv_columns(set(df.columns))
        df = df[df["symbol"].notna() & df["trade_type"].notna()]
        for col in ("quantity", "price"):
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", ""), errors="coerce")
        return df.dropna(subset=["quantity", "price"]).reset_index(drop=True)


kite_service = KiteService()
