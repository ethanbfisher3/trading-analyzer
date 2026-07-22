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


DEFAULT_ACCOUNT_ID = 1


def init_db() -> None:
    """Create all tables if they don't exist yet, and migrate older schemas."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL UNIQUE,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                account_id INTEGER NOT NULL DEFAULT 1,
                key        TEXT NOT NULL,
                value      TEXT NOT NULL,
                PRIMARY KEY (account_id, key)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                account_id   INTEGER NOT NULL DEFAULT 1,
                fingerprint  TEXT NOT NULL,
                symbol       TEXT NOT NULL,
                ticker_type  TEXT NOT NULL DEFAULT 'EQUITY',
                side         TEXT NOT NULL,
                filled_qty   REAL NOT NULL,
                filled_price REAL NOT NULL,
                filled_time  TEXT NOT NULL,
                imported_at  TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (account_id, fingerprint)
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

        _migrate_legacy_schema(conn)

        if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO accounts (id, name) VALUES (?, ?)",
                (DEFAULT_ACCOUNT_ID, "Default"),
            )


def _migrate_legacy_schema(conn: sqlite3.Connection) -> None:
    """
    Upgrade databases created before multi-account support:
    old `orders`/`settings` tables had no account_id column and used a
    single-column primary key. Rebuild them under the new composite-key
    schema, assigning all existing rows to the Default account (id=1).
    """
    orders_cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()}
    if orders_cols and "account_id" not in orders_cols:
        conn.execute("ALTER TABLE orders RENAME TO orders_old")
        conn.execute("""
            CREATE TABLE orders (
                account_id   INTEGER NOT NULL DEFAULT 1,
                fingerprint  TEXT NOT NULL,
                symbol       TEXT NOT NULL,
                ticker_type  TEXT NOT NULL DEFAULT 'EQUITY',
                side         TEXT NOT NULL,
                filled_qty   REAL NOT NULL,
                filled_price REAL NOT NULL,
                filled_time  TEXT NOT NULL,
                imported_at  TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (account_id, fingerprint)
            )
        """)
        conn.execute("""
            INSERT INTO orders (account_id, fingerprint, symbol, ticker_type,
                                 side, filled_qty, filled_price, filled_time, imported_at)
            SELECT 1, fingerprint, symbol, ticker_type, side, filled_qty,
                   filled_price, filled_time, imported_at
            FROM orders_old
        """)
        conn.execute("DROP TABLE orders_old")
        if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO accounts (id, name) VALUES (?, ?)",
                (DEFAULT_ACCOUNT_ID, "Default"),
            )

    settings_cols = {r[1] for r in conn.execute("PRAGMA table_info(settings)").fetchall()}
    if settings_cols and "account_id" not in settings_cols:
        conn.execute("ALTER TABLE settings RENAME TO settings_old")
        conn.execute("""
            CREATE TABLE settings (
                account_id INTEGER NOT NULL DEFAULT 1,
                key        TEXT NOT NULL,
                value      TEXT NOT NULL,
                PRIMARY KEY (account_id, key)
            )
        """)
        conn.execute("""
            INSERT INTO settings (account_id, key, value)
            SELECT 1, key, value FROM settings_old
        """)
        conn.execute("DROP TABLE settings_old")


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

def list_accounts() -> list[dict]:
    """Return all accounts as [{id, name, created_at}, ...], oldest first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, name, created_at FROM accounts ORDER BY id"
        ).fetchall()
    return [{"id": r[0], "name": r[1], "created_at": r[2]} for r in rows]


def create_account(name: str) -> int:
    """Create a new account and return its id. Raises ValueError on duplicate name."""
    name = name.strip()
    if not name:
        raise ValueError("Account name cannot be empty.")
    with _connect() as conn:
        try:
            cur = conn.execute("INSERT INTO accounts (name) VALUES (?)", (name,))
        except sqlite3.IntegrityError:
            raise ValueError(f"An account named '{name}' already exists.")
        return cur.lastrowid


def rename_account(account_id: int, new_name: str) -> None:
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("Account name cannot be empty.")
    with _connect() as conn:
        try:
            conn.execute("UPDATE accounts SET name = ? WHERE id = ?", (new_name, account_id))
        except sqlite3.IntegrityError:
            raise ValueError(f"An account named '{new_name}' already exists.")


def delete_account(account_id: int) -> None:
    """Delete an account and all of its orders/settings. Cannot delete the last account."""
    with _connect() as conn:
        n_accounts = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        if n_accounts <= 1:
            raise ValueError("Cannot delete the only remaining account.")
        conn.execute("DELETE FROM orders WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM settings WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


# ---------------------------------------------------------------------------
# Settings (key-value persistence, scoped per account)
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str = "", account_id: int = DEFAULT_ACCOUNT_ID) -> str:
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE account_id = ? AND key = ?",
            (account_id, key),
        ).fetchone()
    return row[0] if row else default


def save_setting(key: str, value: str, account_id: int = DEFAULT_ACCOUNT_ID) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO settings (account_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(account_id, key) DO UPDATE SET value = excluded.value",
            (account_id, key, value),
        )


# ---------------------------------------------------------------------------
# Order persistence (deduplication, scoped per account)
# ---------------------------------------------------------------------------

def get_known_fingerprints(account_id: int = DEFAULT_ACCOUNT_ID) -> set[str]:
    """Return the set of all order fingerprints already stored for an account."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT fingerprint FROM orders WHERE account_id = ?", (account_id,)
        ).fetchall()
    return {r[0] for r in rows}


def save_new_orders(orders_df: pd.DataFrame, fingerprints: list[str],
                     account_id: int = DEFAULT_ACCOUNT_ID) -> int:
    """
    Insert rows for orders we haven't seen before, under the given account.
    orders_df must have columns: Symbol, Ticker Type, Side, Filled Qty,
                                  Filled Price, Filled Time.
    Returns the number of rows actually inserted.
    """
    if orders_df.empty:
        return 0

    rows = []
    for fp, (_, row) in zip(fingerprints, orders_df.iterrows()):
        rows.append((
            account_id,
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
                (account_id, fingerprint, symbol, ticker_type, side, filled_qty, filled_price, filled_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        inserted = conn.execute(
            "SELECT changes()"
        ).fetchone()[0]

    return inserted


def load_all_orders(account_id: int = DEFAULT_ACCOUNT_ID) -> pd.DataFrame:
    """
    Return every stored order for an account as a DataFrame ready for
    group_orders_into_trades(). Columns match what data_processor expects.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT symbol, ticker_type, side, filled_qty, filled_price, filled_time "
            "FROM orders WHERE account_id = ? ORDER BY filled_time",
            (account_id,),
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


def order_count(account_id: int = DEFAULT_ACCOUNT_ID) -> int:
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM orders WHERE account_id = ?", (account_id,)
        ).fetchone()[0]


def clear_all_data(account_id: int = DEFAULT_ACCOUNT_ID) -> None:
    """Delete all stored orders for an account. Notes/tags are kept."""
    with _connect() as conn:
        conn.execute("DELETE FROM orders WHERE account_id = ?", (account_id,))


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
