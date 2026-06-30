"""
analytics.py
------------
Pure-function statistics calculations over the trades DataFrame.
No side effects, no I/O — just pandas/numpy math.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def calculate_kpis(trades: pd.DataFrame) -> dict:
    """Return a dict of KPI values for the given (filtered) trades."""
    if trades.empty:
        return {}

    winners = trades[trades["net_pnl"] > 0]
    losers  = trades[trades["net_pnl"] < 0]
    breakevens = trades[trades["net_pnl"] == 0]

    total_pnl    = float(trades["net_pnl"].sum())
    total_trades = len(trades)
    win_count    = len(winners)
    loss_count   = len(losers)
    win_rate     = win_count / total_trades * 100 if total_trades else 0.0

    gross_profit = float(winners["net_pnl"].sum()) if not winners.empty else 0.0
    gross_loss   = float(abs(losers["net_pnl"].sum())) if not losers.empty else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    avg_win    = float(winners["net_pnl"].mean()) if not winners.empty else 0.0
    avg_loss   = float(losers["net_pnl"].mean())  if not losers.empty else 0.0
    largest_win  = float(winners["net_pnl"].max()) if not winners.empty else 0.0
    largest_loss = float(losers["net_pnl"].min())  if not losers.empty else 0.0

    # Expectancy = avg_win * win_rate + avg_loss * (1 - win_rate)
    wr = win_rate / 100
    expectancy = (avg_win * wr) + (avg_loss * (1 - wr))

    avg_duration = float(trades["duration_minutes"].mean())

    # Consecutive wins/losses
    streak_win, streak_loss = _streaks(trades["is_winner"].tolist())

    return {
        "total_pnl":      total_pnl,
        "total_trades":   total_trades,
        "win_count":      win_count,
        "loss_count":     loss_count,
        "breakevens":     len(breakevens),
        "win_rate":       win_rate,
        "profit_factor":  profit_factor,
        "gross_profit":   gross_profit,
        "gross_loss":     gross_loss,
        "avg_win":        avg_win,
        "avg_loss":       avg_loss,
        "largest_win":    largest_win,
        "largest_loss":   largest_loss,
        "expectancy":     expectancy,
        "avg_duration":   avg_duration,
        "max_streak_win":  streak_win,
        "max_streak_loss": streak_loss,
    }


def calculate_drawdown(trades: pd.DataFrame) -> pd.DataFrame:
    """
    Return a DataFrame with columns: exit_time, equity, peak, drawdown.
    drawdown is always ≤ 0.
    """
    if trades.empty:
        return pd.DataFrame(columns=["exit_time", "equity", "peak", "drawdown"])

    df = (
        trades.sort_values("exit_time")[["exit_time", "net_pnl"]]
        .copy()
        .reset_index(drop=True)
    )
    df["equity"] = df["net_pnl"].cumsum()
    df["peak"]   = df["equity"].cummax()
    df["drawdown"] = df["equity"] - df["peak"]

    # Insert a zero origin point
    origin = pd.DataFrame([{
        "exit_time": df["exit_time"].iloc[0] - pd.Timedelta(seconds=1),
        "net_pnl":   0.0,
        "equity":    0.0,
        "peak":      0.0,
        "drawdown":  0.0,
    }])
    df = pd.concat([origin, df], ignore_index=True)

    return df


def daily_pnl(trades: pd.DataFrame) -> pd.DataFrame:
    """Return net P&L grouped by calendar date, sorted ascending."""
    return (
        trades.groupby("date")["net_pnl"]
        .sum()
        .reset_index()
        .rename(columns={"net_pnl": "daily_pnl"})
        .sort_values("date")
    )


def symbol_stats(trades: pd.DataFrame) -> pd.DataFrame:
    """Return per-symbol aggregated statistics."""
    agg = trades.groupby("symbol").agg(
        trades_count=("trade_id", "count"),
        winners=("is_winner", "sum"),
        win_rate=("is_winner", "mean"),
        net_pnl=("net_pnl", "sum"),
        avg_pnl=("net_pnl", "mean"),
        best_trade=("net_pnl", "max"),
        worst_trade=("net_pnl", "min"),
        total_volume=("qty", "sum"),
    ).reset_index()
    agg["win_rate"] *= 100
    return agg.sort_values("net_pnl", ascending=False)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _streaks(is_winner: list[bool]) -> tuple[int, int]:
    """Return (max_consecutive_wins, max_consecutive_losses)."""
    max_w = max_l = cur_w = cur_l = 0
    for w in is_winner:
        if w:
            cur_w += 1
            cur_l = 0
        else:
            cur_l += 1
            cur_w = 0
        max_w = max(max_w, cur_w)
        max_l = max(max_l, cur_l)
    return max_w, max_l
