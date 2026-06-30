"""
database.py
-----------
SQLite persistence layer for:
  - orders         raw filled orders (for deduplication and re-grouping)
  - trade_notes    per-trade notes and tags added by the user
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).parent / "trade_journal.db"


def init_db() -> None:
    """Create all tables if they don't exist yet."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                fingerprint  TEXT PRIMARY KEY,
                symbol       TEXT NOT NULL,
                ticker_type  TEXT NOT NULL DEFAULT 'EQUITY',
                side         TEXT NOT NULL,
                filled_qty   REAL NOT NULL,
                filled_price REAL NOT NULL,
                filled_time  TEXT NOT NULL,
                imported_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_notes (
                trade_id   TEXT PRIMARY KEY,
                notes      TEXT    NOT NULL DEFAULT '',
                tags       TEXT    NOT NULL DEFAULT '[]',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


# ---------------------------------------------------------------------------
# Settings (key-value persistence)
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def save_setting(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# ---------------------------------------------------------------------------
# Order persistence (deduplication)
# ---------------------------------------------------------------------------

def get_known_fingerprints() -> set[str]:
    """Return the set of all order fingerprints already stored."""
    with _connect() as conn:
        rows = conn.execute("SELECT fingerprint FROM orders").fetchall()
    return {r[0] for r in rows}


def save_new_orders(orders_df: pd.DataFrame, fingerprints: list[str]) -> int:
    """
    Insert rows for orders we haven't seen before.
    orders_df must have columns: Symbol, Ticker Type, Side, Filled Qty,
                                  Filled Price, Filled Time.
    Returns the number of rows actually inserted.
    """
    if orders_df.empty:
        return 0

    rows = []
    for fp, (_, row) in zip(fingerprints, orders_df.iterrows()):
        rows.append((
            fp,
            str(row["Symbol"]),
            str(row.get("Ticker Type", "EQUITY")),
            str(row["Side"]),
            float(row["Filled Qty"]),
            float(row["Filled Price"]),
            str(row["Filled Time"]),
        ))

    with _connect() as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO orders
                (fingerprint, symbol, ticker_type, side, filled_qty, filled_price, filled_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        inserted = conn.execute(
            "SELECT changes()"
        ).fetchone()[0]

    return inserted


def load_all_orders() -> pd.DataFrame:
    """
    Return every stored order as a DataFrame ready for group_orders_into_trades().
    Columns match what the data_processor expects.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT symbol, ticker_type, side, filled_qty, filled_price, filled_time "
            "FROM orders ORDER BY filled_time"
        ).fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=[
        "Symbol", "Ticker Type", "Side", "Filled Qty", "Filled Price", "Filled Time"
    ])
    df["Filled Time"] = pd.to_datetime(df["Filled Time"], errors="coerce")
    df["Filled Qty"]  = pd.to_numeric(df["Filled Qty"],  errors="coerce")
    df["Filled Price"] = pd.to_numeric(df["Filled Price"], errors="coerce")
    return df.dropna(subset=["Filled Time"]).sort_values("Filled Time").reset_index(drop=True)


def order_count() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]


def clear_all_data() -> None:
    """Delete all stored orders. Notes/tags are kept."""
    with _connect() as conn:
        conn.execute("DELETE FROM orders")


# ---------------------------------------------------------------------------
# Trade notes & tags
# ---------------------------------------------------------------------------

def get_note(trade_id: str) -> dict:
    with _connect() as conn:
        row = conn.execute(
            "SELECT notes, tags FROM trade_notes WHERE trade_id = ?", (trade_id,)
        ).fetchone()
    if row:
        return {"notes": row[0], "tags": json.loads(row[1])}
    return {"notes": "", "tags": []}


def save_note(trade_id: str, notes: str, tags: list[str]) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO trade_notes (trade_id, notes, tags)
            VALUES (?, ?, ?)
            ON CONFLICT(trade_id) DO UPDATE SET
                notes      = excluded.notes,
                tags       = excluded.tags,
                updated_at = CURRENT_TIMESTAMP
            """,
            (trade_id, notes, json.dumps(tags)),
        )


def get_all_notes() -> dict[str, dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT trade_id, notes, tags FROM trade_notes").fetchall()
    return {r[0]: {"notes": r[1], "tags": json.loads(r[2])} for r in rows}


def get_all_tags() -> list[str]:
    with _connect() as conn:
        rows = conn.execute("SELECT tags FROM trade_notes").fetchall()
    tag_set: set[str] = set()
    for (raw,) in rows:
        tag_set.update(json.loads(raw))
    return sorted(tag_set)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
