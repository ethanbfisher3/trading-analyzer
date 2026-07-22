"""
app.py  — Trade Journal & Analytics Dashboard
Run with:  python -m streamlit run app.py
"""

from __future__ import annotations

import datetime
import io
import logging

import pandas as pd
import streamlit as st

from data_processor import (
    load_and_filter_orders,
    group_orders_into_trades,
    compute_fingerprints,
)
from analytics import calculate_kpis, symbol_stats
import database as db
import charts as ch

logging.basicConfig(level=logging.INFO)

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Trade Journal",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Initialise DB ─────────────────────────────────────────────────────────────
db.init_db()

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
.stApp { background-color: #0E0F1A; }

.kpi-card {
    background: #1A1B2E;
    border: 1px solid #2C2D42;
    border-radius: 12px;
    padding: 18px 14px 14px 14px;
    text-align: center;
    min-height: 100px;
    position: relative;   /* keeps tooltip z-index in scope */
}
.kpi-label {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1.2px;
    text-transform: uppercase;
    color: #6B7080;
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
}
.kpi-value          { font-size: 26px; font-weight: 700; color: #DCDCE4; }
.kpi-value.pos      { color: #00C853; }
.kpi-value.neg      { color: #FF1744; }
.kpi-value.neu      { color: #2979FF; }
.kpi-sub            { font-size: 12px; color: #6B7080; margin-top: 5px; }

/* ── Tooltip badge ── */
.kpi-tip {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: #2C2D42;
    color: #8F8FA8;
    font-size: 8px;
    font-weight: 800;
    cursor: help;
    position: relative;
    flex-shrink: 0;
    letter-spacing: 0;
    text-transform: none;
}
.kpi-tip::after {
    content: attr(data-tip);
    position: absolute;
    bottom: calc(100% + 8px);
    left: 50%;
    transform: translateX(-50%);
    background: #252640;
    color: #DCDCE4;
    border: 1px solid #3C3D58;
    border-radius: 8px;
    padding: 9px 12px;
    font-size: 11.5px;
    font-weight: 400;
    line-height: 1.55;
    white-space: normal;
    width: 210px;
    text-align: left;
    z-index: 9999;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.15s ease;
    letter-spacing: 0;
    text-transform: none;
    box-shadow: 0 4px 16px rgba(0,0,0,0.4);
}
.kpi-tip:hover::after { opacity: 1; }

.section-hdr {
    font-size: 15px;
    font-weight: 600;
    color: #DCDCE4;
    border-left: 3px solid #2979FF;
    padding-left: 10px;
    margin: 18px 0 14px 0;
}

[data-testid="stSidebar"] { background-color: #13142A; }
.stTabs [role="tab"]      { font-weight: 500; }
footer                    { visibility: hidden; }
.pos-text { color: #00C853; font-weight: 600; }
.neg-text { color: #FF1744; font-weight: 600; }

/* Rounded card look for every Plotly chart */
[data-testid="stPlotlyChart"] > div {
    border-radius: 12px !important;
    overflow: hidden !important;
    border: 1px solid #2C2D42;
}
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _kpi(col, label: str, value, *, mode: str = "currency", flip: bool = False,
         tip: str = "", subtitle: str = ""):
    if isinstance(value, float) and value != value:
        formatted, css = "—", ""
    else:
        if mode == "currency":
            formatted = f"${value:+,.2f}" if value != 0 else "$0.00"
            css = "pos" if (value > 0) != flip else ("neg" if value != 0 else "")
        elif mode == "percent":
            formatted = f"{value:.1f}%"
            css = "pos" if value >= 55 else "neg" if value < 45 else "neu"
        elif mode == "ratio":
            formatted = "∞" if value == float("inf") else f"{value:.2f}×"
            css = "pos" if value >= 1.5 else "neg" if value < 1.0 else "neu"
        elif mode == "count":
            formatted = f"{int(value):,}"
            css = ""
        elif mode == "duration":
            m = int(value)
            formatted = f"{m // 60}h {m % 60}m" if m >= 60 else f"{m}m"
            css = ""
        else:
            formatted, css = str(value), ""

    tip_html = f'<span class="kpi-tip" data-tip="{tip}">i</span>' if tip else ""
    sub_html  = f'<div class="kpi-sub">{subtitle}</div>' if subtitle else ""
    with col:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">{label}{tip_html}</div>
            <div class="kpi-value {css}">{formatted}</div>
            {sub_html}
        </div>""", unsafe_allow_html=True)


@st.dialog("📅 Day Analysis", width="large")
def _day_detail_popup(sel_date: datetime.date, day_trades: pd.DataFrame) -> None:
    """Modal popup showing KPIs and trade list for a single trading day."""
    day_pnl = day_trades["net_pnl"].sum() if not day_trades.empty else 0.0
    pnl_cls = "pos-text" if day_pnl >= 0 else "neg-text"
    sign    = "+" if day_pnl >= 0 else ""

    st.markdown(
        f"#### {sel_date.strftime('%A, %B %d, %Y')}"
        f" &nbsp;·&nbsp; "
        f"<span class='{pnl_cls}'>{sign}${day_pnl:,.2f}</span>",
        unsafe_allow_html=True,
    )

    if day_trades.empty:
        st.info("No completed trades on this day.")
        return

    day_kpis = calculate_kpis(day_trades)

    dk = st.columns(5, gap="small")
    _kpi(dk[0], "Trades",        day_kpis["total_trades"],  mode="count")
    _kpi(dk[1], "Win Rate",       day_kpis["win_rate"],      mode="percent")
    _kpi(dk[2], "Profit Factor",  day_kpis["profit_factor"], mode="ratio")
    _kpi(dk[3], "Avg Winner",     day_kpis["avg_win"],       mode="currency")
    _kpi(dk[4], "Avg Loser",      day_kpis["avg_loss"],      mode="currency", flip=True)

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    day_display = pd.DataFrame({
        "Symbol":   day_trades["symbol"].values,
        "Dir":      day_trades["direction"].values,
        "Entry":    day_trades["entry_time"].dt.strftime("%H:%M:%S").values,
        "Exit":     day_trades["exit_time"].dt.strftime("%H:%M:%S").values,
        "Qty":      day_trades["qty"].values,
        "Entry $":  day_trades["entry_price"].values,
        "Exit $":   day_trades["exit_price"].values,
        "P&L":      day_trades["net_pnl"].values,
        "Duration": day_trades["duration_minutes"].map(
            lambda m: f"{int(m)//60}h {int(m)%60}m" if int(m) >= 60 else f"{int(m)}m"
        ).values,
    })
    st.dataframe(
        day_display,
        use_container_width=True,
        hide_index=True,
        column_config={
            "P&L":     st.column_config.NumberColumn(format="$%+.2f"),
            "Entry $": st.column_config.NumberColumn(format="$%.4f"),
            "Exit $":  st.column_config.NumberColumn(format="$%.4f"),
            "Qty":     st.column_config.NumberColumn(format="%.0f"),
        },
    )


def _fmt_duration(minutes: float) -> str:
    m = int(minutes)
    return f"{m // 60}h {m % 60}m" if m >= 60 else f"{m}m"


def _fmt_pnl(v: float) -> str:
    return f"${v:+,.2f}"


# ── Import helper (runs once per unique file upload) ──────────────────────────

def _import_file(file_bytes: bytes, filename: str, account_id: int) -> tuple[int, int]:
    """
    Parse uploaded file, deduplicate against the DB for the given account,
    and store only new orders.
    Returns (new_orders_added, duplicates_skipped).
    """
    ext = filename.rsplit(".", 1)[-1].lower()
    source = io.BytesIO(file_bytes) if ext in ("xlsx", "xls") \
        else io.StringIO(file_bytes.decode("utf-8", errors="replace"))

    orders = load_and_filter_orders(source, filename=filename)
    fingerprints = compute_fingerprints(orders)

    known = db.get_known_fingerprints(account_id)
    is_new = [fp not in known for fp in fingerprints]
    new_orders = orders[is_new].reset_index(drop=True)
    new_fps    = [fp for fp, n in zip(fingerprints, is_new) if n]
    dupes      = len(orders) - len(new_orders)

    inserted = db.save_new_orders(new_orders, new_fps, account_id)
    return inserted, dupes


# ── Load all trades from DB (cached, busted when new data arrives) ────────────

@st.cache_data(show_spinner="Grouping trades…")
def _load_trades(account_id: int, db_order_count: int) -> pd.DataFrame:
    """
    Re-group every stored order for an account into trades.
    db_order_count is used only as a cache key — changing it busts the cache.
    """
    all_orders = db.load_all_orders(account_id)
    if all_orders.empty:
        return pd.DataFrame()
    return group_orders_into_trades(all_orders)


@st.cache_data(show_spinner=False)
def _account_summary(account_id: int, db_order_count: int) -> dict:
    """Small stat snapshot for an account, used in the account switcher."""
    trades = _load_trades(account_id, db_order_count)
    if trades.empty:
        return {"trades": 0, "net_pnl": 0.0, "win_rate": 0.0}
    kpis = calculate_kpis(trades)
    return {
        "trades": kpis["total_trades"],
        "net_pnl": kpis["total_pnl"],
        "win_rate": kpis["win_rate"],
    }


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📈 Trade Journal")
    st.divider()

    # ── Accounts ─────────────────────────────────────────────────────────────
    st.markdown("### Accounts")
    accounts = db.list_accounts()
    account_ids = [a["id"] for a in accounts]
    account_names = {a["id"]: a["name"] for a in accounts}

    if st.session_state.get("current_account_id") not in account_ids:
        st.session_state["current_account_id"] = account_ids[0]

    sel_account_id = st.selectbox(
        "Active Account",
        account_ids,
        format_func=lambda aid: account_names[aid],
        index=account_ids.index(st.session_state["current_account_id"]),
    )
    if sel_account_id != st.session_state["current_account_id"]:
        st.session_state["current_account_id"] = sel_account_id
        st.session_state.pop("last_imported", None)
        st.session_state.pop("import_msg", None)
        st.rerun()

    current_account_id = st.session_state["current_account_id"]

    # Quick glance stats for every account, so switching isn't required to compare.
    for acc in accounts:
        acc_orders = db.order_count(acc["id"])
        stats = _account_summary(acc["id"], acc_orders)
        active = acc["id"] == current_account_id
        pnl_cls = "pos-text" if stats["net_pnl"] >= 0 else "neg-text"
        marker = "●" if active else "○"
        name_style = "color:#DCDCE4; font-weight:600;" if active else "color:#6B7080;"
        st.markdown(
            f"<div style='font-size:12px; padding:2px 0 2px 2px;'>"
            f"{marker} <span style='{name_style}'>{acc['name']}</span>"
            f"<span style='color:#6B7080;'> — {stats['trades']} trades · "
            f"</span><span class='{pnl_cls}'>${stats['net_pnl']:,.2f}</span>"
            f"<span style='color:#6B7080;'> · {stats['win_rate']:.0f}% WR</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    with st.expander("⚙️ Manage Accounts"):
        new_acc_name = st.text_input("New account name", placeholder="e.g. Roth IRA",
                                      key="new_account_name")
        if st.button("➕ Add Account", use_container_width=True):
            if new_acc_name.strip():
                try:
                    new_id = db.create_account(new_acc_name)
                    st.session_state["current_account_id"] = new_id
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            else:
                st.warning("Enter a name first.")

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        rename_val = st.text_input(
            "Rename active account", value=account_names[current_account_id],
            key=f"rename_{current_account_id}",
        )
        if st.button("✏️ Rename", use_container_width=True):
            if rename_val.strip() and rename_val.strip() != account_names[current_account_id]:
                try:
                    db.rename_account(current_account_id, rename_val)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

        if len(accounts) > 1:
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
            confirm_del = st.checkbox(
                f"Confirm delete '{account_names[current_account_id]}' and all its trades",
                key=f"confirm_del_{current_account_id}",
            )
            if st.button("🗑 Delete Active Account", type="secondary",
                         use_container_width=True, disabled=not confirm_del):
                db.delete_account(current_account_id)
                st.session_state["current_account_id"] = db.list_accounts()[0]["id"]
                st.session_state.pop("last_imported", None)
                st.session_state.pop("import_msg", None)
                st.cache_data.clear()
                st.rerun()

    st.divider()

    uploaded = st.file_uploader(
        "Upload Webull Export",
        type=["csv", "xlsx", "xls"],
        help="Orders → History → Export in the Webull desktop app.",
    )

    # ── Process upload ────────────────────────────────────────────────────────
    if uploaded:
        # Use (name, size, account) as a cheap session-level dedup key so we
        # don't re-import the same file every time Streamlit reruns.
        file_key = f"{current_account_id}::{uploaded.name}::{len(uploaded.getvalue())}"
        if st.session_state.get("last_imported") != file_key:
            try:
                with st.spinner("Importing…"):
                    added, skipped = _import_file(uploaded.getvalue(), uploaded.name, current_account_id)
                st.session_state["last_imported"] = file_key
                st.session_state["import_msg"] = (added, skipped)
                st.cache_data.clear()
            except Exception as exc:
                st.error(f"Import failed: {exc}")
                st.exception(exc)

        if "import_msg" in st.session_state:
            added, skipped = st.session_state["import_msg"]
            if added > 0:
                st.success(f"✓ {added} new orders imported")
            if skipped > 0:
                st.info(f"{skipped} duplicate orders skipped")
            if added == 0 and skipped > 0:
                st.warning("All orders in this file were already in the journal.")

    st.divider()

    # ── Starting balance ──────────────────────────────────────────────────────
    st.markdown("### Balance")
    stored_balance = float(db.get_setting("starting_balance", "0", account_id=current_account_id))
    new_balance = st.number_input(
        "Starting Balance ($)",
        min_value=0.0,
        value=stored_balance,
        step=500.0,
        format="%.2f",
        help="Your account balance before the first trade. Used to show actual account value on the equity curve and calculate total return %.",
    )
    if new_balance != stored_balance:
        db.save_setting("starting_balance", str(new_balance), account_id=current_account_id)
        st.rerun()

    st.divider()

    # ── Stats & danger zone ───────────────────────────────────────────────────
    n_orders = db.order_count(current_account_id)
    if n_orders > 0:
        st.caption(f"📦 {n_orders} orders stored in journal")
        if st.button("🗑 Clear All Data", type="secondary", use_container_width=True,
                     help="Deletes all stored orders for this account. Notes & tags are kept."):
            db.clear_all_data(current_account_id)
            st.session_state.pop("last_imported", None)
            st.session_state.pop("import_msg", None)
            st.cache_data.clear()
            st.rerun()
    else:
        st.info("Upload a Webull export to get started.")

    st.markdown("<br>", unsafe_allow_html=True)
    st.caption("Trade Journal · Streamlit + Plotly")


# ── Load trades ───────────────────────────────────────────────────────────────
n_orders = db.order_count(current_account_id)

if n_orders == 0:
    st.markdown(f"""
# 📈 Trade Journal & Analytics Dashboard

Upload your **Webull order history** in the sidebar to begin — you're viewing
the **{account_names[current_account_id]}** account.

| View | Contents |
|---|---|
| **Dashboard** | Equity curve · Daily P&L · 12 KPI cards |
| **Analytics** | P&L by day · hour · symbol · drawdown |
| **Trade Journal** | Every round-trip trade with notes & tags |

### How to export from Webull
1. Open the **Webull desktop app**
2. Go to **Orders → Order History**
3. Set your date range and click **Export**
4. Upload the `.xlsx` or `.csv` file above

---
*Re-uploading files that overlap in date is safe — duplicate orders are detected
and skipped automatically.*
    """)
    st.stop()

trades_df = _load_trades(current_account_id, n_orders)

if trades_df is None or trades_df.empty:
    st.warning("Orders are stored but no completed round-trip trades were found yet. "
               "You may have open positions with no matching close.")
    st.stop()


# ── Sidebar filters ───────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### Filters")
    min_d = trades_df["exit_time"].dt.date.min()
    max_d = trades_df["exit_time"].dt.date.max()

    date_range = st.date_input("Date Range", value=(min_d, max_d),
                               min_value=min_d, max_value=max_d)

    all_symbols = sorted(trades_df["symbol"].unique())
    sel_symbols = st.multiselect("Symbols", all_symbols, default=all_symbols)
    sel_dirs    = st.multiselect("Direction", ["LONG", "SHORT"], default=["LONG", "SHORT"])


# Apply filters
if len(date_range) == 2:
    s_d, e_d = date_range
    mask = (
        (trades_df["exit_time"].dt.date >= s_d)
        & (trades_df["exit_time"].dt.date <= e_d)
        & (trades_df["symbol"].isin(sel_symbols))
        & (trades_df["direction"].isin(sel_dirs))
    )
    fdf = trades_df[mask].copy()
else:
    fdf = trades_df.copy()

if fdf.empty:
    st.warning("No trades match the current filters.")
    st.stop()

kpis = calculate_kpis(fdf)
starting_balance = float(db.get_setting("starting_balance", "0", account_id=current_account_id))


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_dash, tab_analytics, tab_journal, tab_year = st.tabs(
    ["📊  Dashboard", "📉  Analytics", "📓  Trade Journal", "📅  Year View"]
)


# ════════════════════════════════════════════════════════════════════════════
# TAB 1 · DASHBOARD
# ════════════════════════════════════════════════════════════════════════════
with tab_dash:
    st.markdown('<div class="section-hdr">Performance Overview</div>', unsafe_allow_html=True)

    pct_return_sub = ""
    if starting_balance > 0:
        pct = kpis["total_pnl"] / starting_balance * 100
        sign = "+" if pct >= 0 else ""
        pct_return_sub = f"{sign}{pct:.2f}% return on ${starting_balance:,.0f}"

    r1 = st.columns(4, gap="small")
    _kpi(r1[0], "Total Net P&L", kpis["total_pnl"], mode="currency",
         tip="Sum of all profits and losses across every closed trade in the selected period. Gross only — Webull exports don&#39;t include commissions.",
         subtitle=pct_return_sub)
    _kpi(r1[1], "Win Rate", kpis["win_rate"], mode="percent",
         tip="Percentage of trades that closed at a profit. 50% = break-even frequency. A high win rate alone doesn&#39;t guarantee profitability — the size of wins vs. losses matters too.")
    _kpi(r1[2], "Profit Factor", kpis["profit_factor"], mode="ratio",
         tip="Gross profit divided by gross loss. Above 1.0 means you made more than you lost in total. 1.5x is solid; 2.0x is strong. Infinity means zero losing trades.")
    _kpi(r1[3], "Total Trades", kpis["total_trades"], mode="count",
         tip="Number of completed round-trip trades (full entry to full exit). A position scaled in across multiple orders still counts as one trade when fully closed.")

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    r2 = st.columns(4, gap="small")
    _kpi(r2[0], "Avg Winner", kpis["avg_win"], mode="currency",
         tip="Average dollar gain on your winning trades. Compare this to Avg Loser — a healthy system wins more per trade than it loses.")
    _kpi(r2[1], "Avg Loser", kpis["avg_loss"], mode="currency", flip=True,
         tip="Average dollar loss on your losing trades. Ideally this is smaller in absolute terms than your Avg Winner. If not, you need a win rate above 50% to be profitable.")
    _kpi(r2[2], "Largest Win", kpis["largest_win"], mode="currency",
         tip="Your single biggest winning trade in the current period. Check whether your overall P&L depends heavily on this one trade.")
    _kpi(r2[3], "Largest Loss", kpis["largest_loss"], mode="currency", flip=True,
         tip="Your single biggest losing trade. Watch for outliers — one large loss can wipe out many small wins. Consider whether a stop-loss would have helped.")

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    r3 = st.columns(4, gap="small")
    _kpi(r3[0], "Expectancy", kpis["expectancy"], mode="currency",
         tip="Average expected profit per trade, combining win rate and trade size: (Win Rate x Avg Win) + (Loss Rate x Avg Loss). Positive = you have a statistical edge. This is the single most important metric.")
    _kpi(r3[1], "Avg Duration", kpis["avg_duration"], mode="duration",
         tip="Average time from trade entry to full exit. Short durations suggest scalping; longer suggest swing trading. Use the P&L by Hour chart to see when you trade best.")
    _kpi(r3[2], "Max Win Streak", kpis["max_streak_win"], mode="count",
         tip="Longest consecutive run of winning trades. Long streaks can encourage overconfidence — stay consistent with your rules regardless of recent results.")
    _kpi(r3[3], "Max Loss Streak", kpis["max_streak_loss"], mode="count",
         tip="Longest consecutive run of losing trades. Use this to gauge how much mental and financial drawdown you&#39;ve had to absorb at worst, and set expectations for future rough patches.")

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    st.plotly_chart(ch.equity_curve(fdf, starting_balance=starting_balance), use_container_width=True)
    st.plotly_chart(ch.daily_pnl_bars(fdf), use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════
# TAB 2 · ANALYTICS
# ════════════════════════════════════════════════════════════════════════════
with tab_analytics:
    col_a, col_b = st.columns(2, gap="medium")
    with col_a:
        st.plotly_chart(ch.pnl_by_day_of_week(fdf), use_container_width=True)
    with col_b:
        st.plotly_chart(ch.pnl_by_hour(fdf), use_container_width=True)

    col_c, col_d = st.columns([3, 1], gap="medium")
    with col_c:
        st.plotly_chart(ch.pnl_by_symbol(fdf), use_container_width=True)
    with col_d:
        st.plotly_chart(ch.win_loss_pie(fdf), use_container_width=True)

    st.plotly_chart(ch.drawdown_chart(fdf), use_container_width=True)

    st.markdown('<div class="section-hdr">Symbol Breakdown</div>', unsafe_allow_html=True)
    sym = symbol_stats(fdf).copy()
    sym_display = pd.DataFrame({
        "Symbol":      sym["symbol"],
        "Trades":      sym["trades_count"].astype(int),
        "Winners":     sym["winners"].astype(int),
        "Win Rate":    sym["win_rate"].map(lambda x: f"{x:.1f}%"),
        "Net P&L":     sym["net_pnl"].map(_fmt_pnl),
        "Avg P&L":     sym["avg_pnl"].map(_fmt_pnl),
        "Best Trade":  sym["best_trade"].map(_fmt_pnl),
        "Worst Trade": sym["worst_trade"].map(_fmt_pnl),
        "Volume":      sym["total_volume"].map(lambda x: f"{x:,.0f}"),
    })
    sym_table_height = 38 + 35 * len(sym_display) + 3
    st.dataframe(sym_display, use_container_width=True, hide_index=True, height=sym_table_height)


# ════════════════════════════════════════════════════════════════════════════
# TAB 3 · TRADE JOURNAL
# ════════════════════════════════════════════════════════════════════════════
with tab_journal:

    all_tags      = db.get_all_tags()
    all_notes_map = db.get_all_notes()

    filter_col, _, search_col = st.columns([2, 0.2, 2])
    with filter_col:
        tag_filter = st.multiselect("Filter by Tag", all_tags) if all_tags else []
    with search_col:
        sym_search = st.text_input("Search Symbol", placeholder="e.g. TSLA")

    j = fdf[["trade_id", "symbol", "direction", "entry_time", "exit_time",
             "qty", "entry_price", "exit_price", "net_pnl", "duration_minutes"]].copy()

    j["_notes"] = j["trade_id"].map(lambda tid: all_notes_map.get(tid, {}).get("notes", ""))
    j["_tags"]  = j["trade_id"].map(lambda tid: all_notes_map.get(tid, {}).get("tags", []))

    if tag_filter:
        j = j[j["_tags"].apply(lambda t: any(tag in t for tag in tag_filter))]
    if sym_search:
        j = j[j["symbol"].str.upper().str.contains(sym_search.upper())]

    if j.empty:
        st.info("No trades match the current filters.")
    else:
        display_j = pd.DataFrame({
            "Symbol":     j["symbol"],
            "Dir":        j["direction"],
            "Entry Time": j["entry_time"].dt.strftime("%Y-%m-%d %H:%M"),
            "Exit Time":  j["exit_time"].dt.strftime("%Y-%m-%d %H:%M"),
            "Qty":        j["qty"],
            "Entry $":    j["entry_price"],
            "Exit $":     j["exit_price"],
            "P&L":        j["net_pnl"],
            "Duration":   j["duration_minutes"].map(_fmt_duration),
            "Tags":       j["_tags"].map(lambda t: ", ".join(t) if t else ""),
            "Notes":      j["_notes"].map(lambda n: n[:60] + "…" if len(n) > 60 else n),
        }).reset_index(drop=True)

        st.markdown('<div class="section-hdr">Trade Log</div>', unsafe_allow_html=True)

        event = st.dataframe(
            display_j,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "P&L":     st.column_config.NumberColumn(format="$%+.2f"),
                "Entry $": st.column_config.NumberColumn(format="$%.4f"),
                "Exit $":  st.column_config.NumberColumn(format="$%.4f"),
                "Qty":     st.column_config.NumberColumn(format="%.0f"),
            },
        )

        selected_rows = event.selection.rows if hasattr(event, "selection") else []

        if selected_rows:
            idx = selected_rows[0]
            trade_row = j.iloc[idx]
            tid = trade_row["trade_id"]
            note_data = db.get_note(tid)

            st.markdown("---")
            pnl_color = "pos-text" if trade_row["net_pnl"] >= 0 else "neg-text"
            st.markdown(
                f"### ✏️ {trade_row['symbol']} &nbsp;·&nbsp; {trade_row['direction']} &nbsp;·&nbsp; "
                f"<span class='{pnl_color}'>{_fmt_pnl(trade_row['net_pnl'])}</span>",
                unsafe_allow_html=True,
            )

            info_cols = st.columns(4)
            info_cols[0].metric("Entry",    f"${trade_row['entry_price']:.4f}")
            info_cols[1].metric("Exit",     f"${trade_row['exit_price']:.4f}")
            info_cols[2].metric("Qty",      f"{trade_row['qty']:,.0f}")
            info_cols[3].metric("Duration", _fmt_duration(trade_row["duration_minutes"]))

            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            note_col, tag_col = st.columns([3, 1])

            PRESET_TAGS = [
                "Breakout", "Breakdown", "FOMO", "Reversal", "Trend Follow",
                "Mean Revert", "Scalp", "News Play", "Gap Up", "Gap Down",
                "VWAP Reclaim", "Support", "Resistance", "Good Entry",
                "Bad Entry", "Good Exit", "Left Early", "Overtraded",
                "Stopped Out", "Runner", "Size Too Large", "Size Too Small",
            ]

            with note_col:
                new_notes = st.text_area(
                    "Notes",
                    value=note_data["notes"],
                    height=140,
                    placeholder="Describe the setup, execution, what you'd do differently…",
                    key=f"notes_{tid}",
                )

            with tag_col:
                existing = note_data["tags"]
                new_tags = st.multiselect(
                    "Tags",
                    options=sorted(set(PRESET_TAGS + all_tags + existing)),
                    default=existing,
                    key=f"tags_{tid}",
                )

            if st.button("💾 Save", type="primary", key=f"save_{tid}"):
                db.save_note(tid, new_notes, new_tags)
                st.success("Saved!")
                st.rerun()
        else:
            st.markdown(
                "<div style='color:#6B7080; font-size:13px; padding:12px 0;'>"
                "Click any row above to add notes and tags to that trade."
                "</div>",
                unsafe_allow_html=True,
            )


# ════════════════════════════════════════════════════════════════════════════
# TAB 4 · YEAR VIEW
# ════════════════════════════════════════════════════════════════════════════
with tab_year:
    all_years = sorted(trades_df["exit_time"].dt.year.unique(), reverse=True)
    if len(all_years) > 1:
        sel_year = int(st.selectbox("Year", all_years, key="year_sel"))
    else:
        sel_year = int(all_years[0])
        st.markdown(
            f'<div class="section-hdr">{sel_year}</div>',
            unsafe_allow_html=True,
        )

    # Year-level 90th-percentile for consistent colour scaling across months
    yr_trades = trades_df[trades_df["exit_time"].dt.year == sel_year]
    _abs = [abs(v) for v in
            yr_trades.groupby(yr_trades["exit_time"].dt.date)["net_pnl"].sum()
            if v != 0]
    year_p90 = float(pd.Series(_abs).quantile(0.9)) if _abs else 1.0

    # Persist the last-clicked date so the highlight survives dialog open/close
    _date_ss = f"_cal_date_{sel_year}"
    highlight_date = st.session_state.get(_date_ss)

    st.caption("Click any green or red day to see that day's trades.")

    # ── 4 rows × 3 columns — native st.plotly_chart with on_select ───────────
    clicked_date: datetime.date | None = None

    for row_months in ([1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12]):
        cols = st.columns(3, gap="small")
        for col, month in zip(cols, row_months):
            with col:
                fig = ch.month_calendar(
                    trades_df, sel_year, month,
                    p90=year_p90,
                    highlight_date=highlight_date,
                )
                result = st.plotly_chart(
                    fig,
                    use_container_width=True,
                    on_select="rerun",
                    key=f"cal_{sel_year}_{month}",
                )
                if clicked_date is None and result and result.selection.points:
                    raw = result.selection.points[0].get("customdata")
                    if isinstance(raw, list):
                        raw = raw[0] if raw else None
                    if raw:
                        try:
                            clicked_date = datetime.date.fromisoformat(str(raw))
                        except ValueError:
                            pass

    # ── Open popup for the clicked day ────────────────────────────────────────
    if clicked_date:
        st.session_state[_date_ss] = clicked_date
        day_trades_modal = trades_df[trades_df["exit_time"].dt.date == clicked_date]
        _day_detail_popup(clicked_date, day_trades_modal)
    elif highlight_date:
        # "View Details" lets the user reopen the popup without re-clicking
        if st.button("📊 View Details", key="year_reopen", type="secondary"):
            day_trades_modal = trades_df[trades_df["exit_time"].dt.date == highlight_date]
            _day_detail_popup(highlight_date, day_trades_modal)
    else:
        st.markdown(
            "<div style='color:#6B7080; font-size:13px; padding:8px 0;'>"
            "Click any green or red day on the calendar above."
            "</div>",
            unsafe_allow_html=True,
        )
