"""
data_processor.py
-----------------
Loads a Webull CSV export, filters to FILLED orders only, then groups
individual orders into completed round-trip trades using FIFO lot matching.

FIFO Algorithm
--------------
Per symbol, orders are processed in chronological order:

  Opening leg  → append {qty, price, time} to a deque (open_lots)
                  and to entry_lots for the current trade.

  Closing leg  → consume from the front of open_lots, building exit_lots.
                  When open_lots empties the accumulated entry_lots + exit_lots
                  define one completed Trade.

  Reversal     → if a closing order exceeds the open position the surplus qty
                  immediately opens a new trade in the opposite direction.
"""

from __future__ import annotations

import hashlib
import io
import logging
from collections import deque
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Exact column names Webull uses
REQUIRED_COLUMNS = {
    "Symbol", "Side", "Filled Qty", "Filled Price",
    "Filled Time", "Execute Status", "Ticker Type",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_and_filter_orders(source, filename: str = "") -> pd.DataFrame:
    """
    Read a Webull export — accepts CSV or Excel (.xlsx/.xls).
    source can be a file path string, StringIO, or BytesIO.
    Keeps only FILLED rows and coerces columns to useful types.
    Raises ValueError if required columns are missing.
    """
    ext = Path(filename).suffix.lower() if filename else ""
    is_excel = ext in (".xlsx", ".xls") or isinstance(source, (bytes, io.BytesIO))

    if is_excel:
        buf = io.BytesIO(source) if isinstance(source, bytes) else source
        df = pd.read_excel(buf, sheet_name=0, engine="openpyxl")
    else:
        df = pd.read_csv(source)

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            f"File is missing expected columns: {sorted(missing)}\n"
            f"Found: {sorted(df.columns)}"
        )

    # Keep only filled orders
    df = df[df["Execute Status"].astype(str).str.upper() == "FILLED"].copy()

    if df.empty:
        raise ValueError("No FILLED orders found in the uploaded file.")

    # Parse datetimes (Webull uses various timezone-aware formats)
    for col in ["Filled Time", "Placed Time", "Create Time"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
            # Convert to local naive for display clarity
            df[col] = df[col].dt.tz_localize(None) if df[col].dt.tz is None else df[col].dt.tz_convert("US/Eastern").dt.tz_localize(None)

    # Numeric coercion
    for col in ["Filled Qty", "Filled Price", "Total Qty", "Filled Amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    df = df.dropna(subset=["Filled Time", "Symbol", "Side", "Filled Qty", "Filled Price"])
    df = df[df["Filled Qty"] > 0]
    df = df.sort_values("Filled Time").reset_index(drop=True)

    logger.info("Loaded %d filled orders across %d symbols.", len(df), df["Symbol"].nunique())
    return df


def compute_fingerprints(orders_df: pd.DataFrame) -> list[str]:
    """
    Return a SHA-256 fingerprint for each row in orders_df.
    Two rows with the same Symbol, Side, Filled Time, Filled Qty, and
    Filled Price will always produce the same fingerprint — used to detect
    duplicate orders across multiple file uploads.
    """
    fps = []
    for _, row in orders_df.iterrows():
        key = (
            f"{row['Symbol']}|{row['Side']}|{row['Filled Time']}"
            f"|{float(row['Filled Qty']):.6f}|{float(row['Filled Price']):.6f}"
        )
        fps.append(hashlib.sha256(key.encode()).hexdigest()[:24])
    return fps


def group_orders_into_trades(orders_df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert a DataFrame of individual orders into a DataFrame of
    completed round-trip trades (one row per trade).
    Open positions remaining at the end of the data are logged and skipped.
    """
    all_trades: list[dict] = []

    for symbol, sym_orders in orders_df.groupby("Symbol"):
        sym_orders = sym_orders.sort_values("Filled Time").reset_index(drop=True)

        # Determine the options multiplier (100x for options contracts)
        ticker_type = str(sym_orders["Ticker Type"].iloc[0]).upper()
        multiplier = 100 if ticker_type in ("OPT", "OPTION") else 1

        # State for the current open trade
        open_lots: deque[dict] = deque()   # {qty, price, time}  — FIFO queue
        entry_lots: list[dict] = []
        exit_lots: list[dict] = []
        net_position: float = 0.0          # +long, -short
        direction: Optional[str] = None

        for _, order in sym_orders.iterrows():
            side = _normalize_side(order["Side"])
            qty = float(order["Filled Qty"])
            price = float(order["Filled Price"])
            time = order["Filled Time"]

            if qty <= 0 or price <= 0:
                continue

            sign = 1.0 if side == "BUY" else -1.0

            # Determine if this order is opening or closing the position
            is_opening = (
                abs(net_position) < 1e-9            # flat — any order opens
                or (net_position > 0 and side == "BUY")   # adding to long
                or (net_position < 0 and side == "SELL")  # adding to short
            )

            if is_opening:
                lot = {"qty": qty, "price": price, "time": time}
                open_lots.append(lot)
                entry_lots.append(lot.copy())
                if direction is None:
                    direction = "LONG" if side == "BUY" else "SHORT"
                net_position += sign * qty

            else:
                # Closing (or reversing) the position
                remaining = qty

                while remaining > 1e-9 and open_lots:
                    front = open_lots[0]
                    matched = min(remaining, front["qty"])
                    exit_lots.append({"qty": matched, "price": price, "time": time})
                    front["qty"] -= matched
                    remaining -= matched
                    if front["qty"] < 1e-9:
                        open_lots.popleft()

                net_position += sign * qty  # arithmetic always correct

                if not open_lots:
                    # Position fully closed → record the trade
                    trade = _build_trade(
                        symbol=symbol,
                        direction=direction,
                        entry_lots=entry_lots,
                        exit_lots=exit_lots,
                        multiplier=multiplier,
                    )
                    all_trades.append(trade)

                    # Reset state
                    entry_lots = []
                    exit_lots = []
                    direction = None

                    # Reversal: surplus qty opens a new position
                    if remaining > 1e-9:
                        direction = "LONG" if side == "BUY" else "SHORT"
                        reversal_lot = {"qty": remaining, "price": price, "time": time}
                        open_lots.append(reversal_lot)
                        entry_lots = [reversal_lot.copy()]
                        # net_position already reflects the reversal from += above

        if entry_lots:
            logger.info(
                "Symbol %s has an open position at end of data (%+.2f shares) — excluded.",
                symbol, net_position,
            )

    if not all_trades:
        return pd.DataFrame()

    df = pd.DataFrame(all_trades)
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df["exit_time"] = pd.to_datetime(df["exit_time"])
    df["date"] = df["exit_time"].dt.date
    df["day_of_week"] = df["exit_time"].dt.day_name()
    df["hour_of_day"] = df["exit_time"].dt.hour
    df["is_winner"] = df["net_pnl"] > 0
    df = df.sort_values("exit_time").reset_index(drop=True)
    df["cumulative_pnl"] = df["net_pnl"].cumsum()

    logger.info("Grouped %d orders → %d completed trades.", len(orders_df), len(df))
    return df


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_side(side: str) -> str:
    """Map Webull side strings to 'BUY' or 'SELL'."""
    s = str(side).upper().replace(" ", "_").replace("-", "_")
    if s in ("BUY", "BUY_TO_COVER", "BTC"):
        return "BUY"
    if s in ("SELL", "SELL_SHORT", "SS", "SHORT", "STO", "STC"):
        return "SELL"
    # Best-effort fallback
    return "BUY" if "BUY" in s else "SELL"


def _weighted_avg(lots: list[dict]) -> float:
    total_qty = sum(l["qty"] for l in lots)
    if total_qty < 1e-9:
        return 0.0
    return sum(l["qty"] * l["price"] for l in lots) / total_qty


def _build_trade(
    symbol: str,
    direction: str,
    entry_lots: list[dict],
    exit_lots: list[dict],
    multiplier: int = 1,
) -> dict:
    """Assemble a trade record from matched lots."""
    total_qty = sum(l["qty"] for l in entry_lots)
    avg_entry = _weighted_avg(entry_lots)
    avg_exit = _weighted_avg(exit_lots)

    if direction == "LONG":
        gross_pnl = (avg_exit - avg_entry) * total_qty * multiplier
    else:
        gross_pnl = (avg_entry - avg_exit) * total_qty * multiplier

    entry_time = min(l["time"] for l in entry_lots)
    exit_time = max(l["time"] for l in exit_lots)
    duration_min = max((exit_time - entry_time).total_seconds() / 60, 0)

    # Stable ID derived from the trade's key attributes so that re-grouping
    # the same orders always produces the same ID (preserving saved notes).
    id_key = f"{symbol}|{direction}|{entry_time}|{exit_time}|{total_qty:.6f}"
    trade_id = hashlib.sha256(id_key.encode()).hexdigest()[:32]

    return {
        "trade_id": trade_id,
        "symbol": symbol,
        "direction": direction,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "qty": round(total_qty, 6),
        "entry_price": round(avg_entry, 4),
        "exit_price": round(avg_exit, 4),
        "multiplier": multiplier,
        "gross_pnl": round(gross_pnl, 2),
        "net_pnl": round(gross_pnl, 2),   # no commission data in Webull export
        "duration_minutes": round(duration_min, 2),
    }
