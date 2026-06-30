"""
charts.py
---------
Plotly chart builders. Each function accepts a trades DataFrame
(already filtered) and returns a go.Figure ready for st.plotly_chart().
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

from analytics import calculate_drawdown, daily_pnl, symbol_stats

# ── Colour palette ──────────────────────────────────────────────────────────
C = {
    "green":   "#00C853",
    "red":     "#FF1744",
    "blue":    "#2979FF",
    "amber":   "#FFB300",
    "surface": "#1A1B2E",
    "grid":    "#2C2D42",
    "text":    "#DCDCE4",
    "subtext": "#8F8FA8",
}


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _base_layout(title: str, height: int = 380) -> dict:
    return dict(
        title=dict(text=title, font=dict(color=C["text"], size=15, family="Inter, sans-serif")),
        paper_bgcolor=C["surface"],
        plot_bgcolor=C["surface"],
        font=dict(color=C["text"], family="Inter, sans-serif"),
        xaxis=dict(gridcolor=C["grid"], zerolinecolor=C["grid"], showline=False),
        yaxis=dict(gridcolor=C["grid"], zerolinecolor=C["grid"], showline=False),
        margin=dict(l=60, r=20, t=50, b=50),
        height=height,
        legend=dict(bgcolor="rgba(0,0,0,0)", borderwidth=0),
        hovermode="x unified",
    )


# ── Individual charts ───────────────────────────────────────────────────────

def equity_curve(trades: pd.DataFrame, starting_balance: float = 0.0) -> go.Figure:
    df = trades.sort_values("exit_time").copy()
    df["pnl_cumsum"] = df["net_pnl"].cumsum()
    df["equity"] = starting_balance + df["pnl_cumsum"]

    origin = pd.DataFrame([{
        "exit_time":  df["exit_time"].iloc[0] - pd.Timedelta(seconds=1),
        "equity":     starting_balance,
        "pnl_cumsum": 0.0,
    }])
    df = pd.concat([origin, df[["exit_time", "equity", "pnl_cumsum"]]], ignore_index=True)

    final_equity = df["equity"].iloc[-1]
    line_color = C["green"] if final_equity >= starting_balance else C["red"]

    has_balance = starting_balance > 0

    if has_balance:
        hover = (
            "%{x|%b %d %H:%M}<br>"
            "<b>Balance: $%{y:,.2f}</b><br>"
            "P&L: $%{customdata:+,.2f}"
            "<extra></extra>"
        )
        customdata = df["pnl_cumsum"]
        title = "Account Balance"
        y_label = "Account Value ($)"
    else:
        hover = "%{x|%b %d %H:%M}<br><b>P&L: $%{y:+,.2f}</b><extra></extra>"
        customdata = df["equity"]   # unused but required
        title = "Cumulative P&L"
        y_label = "Cumulative P&L ($)"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["exit_time"],
        y=df["equity"],
        customdata=customdata,
        mode="lines",
        line=dict(color=line_color, width=2),
        fill="tozeroy" if not has_balance else "tonexty",
        fillcolor=_rgba(line_color, 0.10),
        name=title,
        hovertemplate=hover,
    ))

    if has_balance:
        # Shade the area above the starting balance line in green/red
        fig.add_hline(
            y=starting_balance,
            line_width=1,
            line_dash="dot",
            line_color="#8F8FA8",
            annotation_text=f"  Start  ${starting_balance:,.0f}",
            annotation_font_color="#8F8FA8",
            annotation_font_size=11,
        )

    layout = _base_layout(title, height=340)
    layout["yaxis"]["title"] = y_label
    fig.update_layout(**layout)
    return fig


def daily_pnl_bars(trades: pd.DataFrame) -> go.Figure:
    df = daily_pnl(trades)
    df["date_dt"] = pd.to_datetime(df["date"])
    colors = [C["green"] if v >= 0 else C["red"] for v in df["daily_pnl"]]

    fig = go.Figure(go.Bar(
        x=df["date_dt"],
        y=df["daily_pnl"],
        marker_color=colors,
        hovertemplate="%{x|%b %d, %Y}<br><b>$%{y:+,.2f}</b><extra></extra>",
    ))
    fig.update_layout(**_base_layout("Daily P&L", height=280))
    fig.add_hline(y=0, line_width=1, line_color=C["grid"])
    return fig


def pnl_by_day_of_week(trades: pd.DataFrame) -> go.Figure:
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    by_day = trades.groupby("day_of_week")["net_pnl"].sum().reindex(order).fillna(0)
    colors = [C["green"] if v >= 0 else C["red"] for v in by_day]

    fig = go.Figure(go.Bar(
        x=by_day.index,
        y=by_day.values,
        marker_color=colors,
        hovertemplate="%{x}<br><b>$%{y:+,.2f}</b><extra></extra>",
    ))
    fig.update_layout(**_base_layout("P&L by Day of Week", height=320))
    fig.add_hline(y=0, line_width=1, line_color=C["grid"])
    return fig


def pnl_by_hour(trades: pd.DataFrame) -> go.Figure:
    by_hour = trades.groupby("hour_of_day")["net_pnl"].sum().sort_index()
    hour_labels = [f"{h:02d}:00" for h in by_hour.index]
    colors = [C["green"] if v >= 0 else C["red"] for v in by_hour]

    fig = go.Figure(go.Bar(
        x=hour_labels,
        y=by_hour.values,
        marker_color=colors,
        hovertemplate="Hour %{x}<br><b>$%{y:+,.2f}</b><extra></extra>",
    ))
    fig.update_layout(**_base_layout("P&L by Hour of Day (ET)", height=320))
    fig.add_hline(y=0, line_width=1, line_color=C["grid"])
    return fig


def pnl_by_symbol(trades: pd.DataFrame) -> go.Figure:
    stats = symbol_stats(trades).sort_values("net_pnl", ascending=True)
    colors = [C["green"] if v >= 0 else C["red"] for v in stats["net_pnl"]]

    fig = go.Figure(go.Bar(
        x=stats["net_pnl"],
        y=stats["symbol"],
        orientation="h",
        marker_color=colors,
        customdata=np.column_stack([
            stats["trades_count"],
            stats["win_rate"].round(1),
        ]),
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Net P&L: $%{x:+,.2f}<br>"
            "Trades: %{customdata[0]}<br>"
            "Win Rate: %{customdata[1]}%"
            "<extra></extra>"
        ),
    ))
    h = max(300, len(stats) * 28 + 80)
    fig.update_layout(**_base_layout("P&L by Symbol", height=h))
    return fig


def drawdown_chart(trades: pd.DataFrame) -> go.Figure:
    dd = calculate_drawdown(trades)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dd["exit_time"],
        y=dd["drawdown"],
        mode="lines",
        fill="tozeroy",
        line=dict(color=C["red"], width=1.5),
        fillcolor=_rgba(C["red"], 0.15),
        name="Drawdown",
        hovertemplate="%{x|%b %d}<br><b>$%{y:,.2f}</b><extra></extra>",
    ))
    max_dd = dd["drawdown"].min()
    max_dd_time = dd.loc[dd["drawdown"].idxmin(), "exit_time"]
    fig.add_annotation(
        x=max_dd_time, y=max_dd,
        text=f"Max DD: ${max_dd:,.2f}",
        showarrow=True, arrowhead=2,
        font=dict(color=C["red"], size=11),
        bgcolor=C["surface"], bordercolor=C["red"],
    )
    fig.update_layout(**_base_layout("Drawdown", height=280))
    return fig


def month_calendar(trades: pd.DataFrame, year: int, month: int,
                   p90: float = 1.0, highlight_date=None) -> go.Figure:
    """
    Single-month mini-calendar as a standalone Plotly figure.
    p90: 90th-percentile abs(P&L) for the year — passed in so all months
         use a consistent colour scale.
    customdata per point = ISO date string.
    """
    import calendar as _cal
    import datetime

    MONTHS = [
        'January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December',
    ]

    mask = (trades['exit_time'].dt.year == year) & (trades['exit_time'].dt.month == month)
    mdf  = trades[mask]
    daily: dict[datetime.date, float] = (
        mdf.groupby(mdf['exit_time'].dt.date)['net_pnl'].sum().to_dict()
        if not mdf.empty else {}
    )
    month_total = sum(daily.values())

    p90 = max(p90, 0.01)

    def _cell_color(pnl):
        if pnl is None:
            return '#1E2035'
        a = min(0.25 + 0.75 * abs(pnl) / p90, 1.0)
        return f'rgba(0,200,83,{a:.2f})' if pnl > 0 else f'rgba(255,23,68,{a:.2f})'

    def _text_color(pnl):
        if pnl is None:
            return '#4A5068'
        a = min(0.25 + 0.75 * abs(pnl) / p90, 1.0)
        return '#0E0F1A' if a > 0.5 else '#DCDCE4'

    first_wd, n_days = _cal.monthrange(year, month)
    offset = (first_wd + 1) % 7   # Sunday-first

    xs, ys, colors, texts, text_colors, customs, hovers = [], [], [], [], [], [], []
    outline_colors, outline_widths = [], []

    for day in range(1, n_days + 1):
        cell   = offset + day - 1
        x, y   = cell % 7, -(cell // 7)
        d      = datetime.date(year, month, day)
        pnl    = daily.get(d)
        if pnl is None:
            hover = f'<b>{d.strftime("%B %d, %Y")}</b><br>No trades'
        elif pnl >= 0:
            hover = f'<b>{d.strftime("%B %d, %Y")}</b><br>+${pnl:,.2f}'
        else:
            hover = f'<b>{d.strftime("%B %d, %Y")}</b><br>-${abs(pnl):,.2f}'
        xs.append(x);                           ys.append(y)
        colors.append(_cell_color(pnl));        texts.append(str(day))
        text_colors.append(_text_color(pnl));   customs.append(d.isoformat())
        hovers.append(hover)
        is_sel = (highlight_date is not None and d == highlight_date)
        outline_colors.append('#FFFFFF' if is_sel else 'rgba(0,0,0,0)')
        outline_widths.append(2.5 if is_sel else 0)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=xs, y=ys,
        mode='markers+text',
        marker=dict(
            symbol='square', size=36, color=colors,
            line=dict(color=outline_colors, width=outline_widths),
        ),
        text=texts,
        textposition='middle center',
        textfont=dict(size=12, color=text_colors),
        customdata=customs,
        hovertemplate='%{hovertext}<extra></extra>',
        hovertext=hovers,
        showlegend=False,
        name='',
    ))

    # Month name (top-left) + coloured P&L total (top-right) as annotations
    # so they sit in the margin above the DOW tick labels — no overlap possible.
    pnl_color = C['green'] if month_total > 0 else (C['red'] if month_total < 0 else C['subtext'])
    pnl_text  = (f'+${month_total:,.0f}' if month_total > 0 else
                 f'-${abs(month_total):,.0f}' if month_total < 0 else '')

    fig.add_annotation(
        text=f'<b>{MONTHS[month - 1]}</b>',
        xref='paper', yref='paper',
        x=0.04, y=1.0, xanchor='left', yanchor='bottom',
        showarrow=False,
        font=dict(size=13, color=C['text'], family='Inter, sans-serif'),
    )
    if pnl_text:
        fig.add_annotation(
            text=f'<b>{pnl_text}</b>',
            xref='paper', yref='paper',
            x=0.96, y=1.0, xanchor='right', yanchor='bottom',
            showarrow=False,
            font=dict(size=11, color=pnl_color, family='Inter, sans-serif'),
        )

    fig.update_xaxes(
        tickmode='array',
        tickvals=list(range(7)),
        ticktext=['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'],
        tickfont=dict(size=9.5, color=C['subtext']),
        side='top',
        showgrid=False,
        zeroline=False,
        range=[-0.65, 6.65],
        fixedrange=True,
    )
    fig.update_yaxes(visible=False, range=[-5.65, 0.65], fixedrange=True)

    fig.update_layout(
        paper_bgcolor='#1A1B2E',
        plot_bgcolor='#1A1B2E',
        height=280,
        margin=dict(l=6, r=6, t=46, b=6),
        showlegend=False,
        dragmode=False,
        hoverlabel=dict(
            bgcolor='#252640',
            bordercolor='#3C3D58',
            font=dict(size=12, color=C['text']),
        ),
    )
    return fig


def year_calendar(trades: pd.DataFrame, year: int) -> go.Figure:
    """
    4×3 grid of monthly mini-calendars for *year*, coloured by daily P&L.
    Supports point-click selection via st.plotly_chart(on_select="rerun").
    customdata per point = ISO date string (e.g. "2024-03-15").
    """
    import calendar as _cal
    import datetime
    from plotly.subplots import make_subplots

    MONTH_NAMES = [
        'January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December',
    ]

    # ── Daily P&L lookup ────────────────────────────────────────────────────
    yr = trades[trades['exit_time'].dt.year == year]
    daily: dict[datetime.date, float] = (
        yr.groupby(yr['exit_time'].dt.date)['net_pnl'].sum().to_dict()
        if not yr.empty else {}
    )

    # Use 90th-percentile absolute value for intensity scaling so one huge
    # outlier doesn't wash out all other cells.
    abs_vals = [abs(v) for v in daily.values() if v != 0]
    p90 = max(float(np.percentile(abs_vals, 90)) if abs_vals else 1.0, 0.01)

    def _cell_color(pnl):
        if pnl is None:
            return '#1E2035'
        a = min(0.25 + 0.75 * abs(pnl) / p90, 1.0)
        return f'rgba(0,200,83,{a:.2f})' if pnl > 0 else f'rgba(255,23,68,{a:.2f})'

    def _text_color(pnl):
        if pnl is None:
            return '#4A5068'
        a = min(0.25 + 0.75 * abs(pnl) / p90, 1.0)
        return '#0E0F1A' if a > 0.5 else '#DCDCE4'

    # ── Monthly totals for subplot titles ───────────────────────────────────
    def _subplot_title(m: int) -> str:
        tot = sum(v for d, v in daily.items() if d.month == m)
        name = MONTH_NAMES[m - 1]
        if tot > 0:
            return f'{name}  +${tot:,.0f}'
        if tot < 0:
            return f'{name}  -${abs(tot):,.0f}'
        return name

    fig = make_subplots(
        rows=4, cols=3,
        subplot_titles=[_subplot_title(m) for m in range(1, 13)],
        vertical_spacing=0.07,
        horizontal_spacing=0.04,
    )

    for mi in range(12):
        month = mi + 1
        row, col_idx = mi // 3 + 1, mi % 3 + 1

        first_wd, n_days = _cal.monthrange(year, month)
        offset = (first_wd + 1) % 7   # Sunday-first (Mon=0→offset 1, Sun=6→offset 0)

        xs, ys, colors, day_texts, text_colors, customs, hovers = [], [], [], [], [], [], []

        for day in range(1, n_days + 1):
            cell = offset + day - 1
            x = cell % 7
            y = -(cell // 7)   # week 0 at top (y=0), week 5 at bottom (y=-5)

            d = datetime.date(year, month, day)
            pnl = daily.get(d)

            if pnl is None:
                hover = f'<b>{d.strftime("%B %d, %Y")}</b><br>No trades'
            elif pnl >= 0:
                hover = f'<b>{d.strftime("%B %d, %Y")}</b><br>+${pnl:,.2f}'
            else:
                hover = f'<b>{d.strftime("%B %d, %Y")}</b><br>-${abs(pnl):,.2f}'

            xs.append(x)
            ys.append(y)
            colors.append(_cell_color(pnl))
            day_texts.append(str(day))
            text_colors.append(_text_color(pnl))
            customs.append(d.isoformat())
            hovers.append(hover)

        fig.add_trace(
            go.Scatter(
                x=xs, y=ys,
                mode='markers+text',
                marker=dict(
                    symbol='square',
                    size=32,
                    color=colors,
                    line=dict(width=0),
                ),
                text=day_texts,
                textposition='middle center',
                textfont=dict(size=11, color=text_colors),
                customdata=customs,
                hovertemplate='%{hovertext}<extra></extra>',
                hovertext=hovers,
                showlegend=False,
                name='',
                selected=dict(marker=dict(opacity=1.0, size=36)),
                unselected=dict(marker=dict(opacity=0.45)),
            ),
            row=row, col=col_idx,
        )

    fig.update_xaxes(
        tickmode='array',
        tickvals=list(range(7)),
        ticktext=['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'],
        tickfont=dict(size=8.5, color=C['subtext']),
        side='top',
        showgrid=False,
        zeroline=False,
        range=[-0.65, 6.65],
        fixedrange=True,
    )
    fig.update_yaxes(visible=False, range=[-5.65, 0.65], fixedrange=True)

    fig.update_layout(
        paper_bgcolor='#0E0F1A',
        plot_bgcolor='#0E0F1A',
        font=dict(color=C['text'], family='Inter, sans-serif'),
        height=920,
        margin=dict(l=10, r=10, t=80, b=20),
        showlegend=False,
        dragmode='select',
        clickmode='event+select',
        hoverlabel=dict(
            bgcolor='#252640',
            bordercolor='#3C3D58',
            font=dict(size=12, color=C['text']),
        ),
    )

    for ann in fig.layout.annotations:
        ann.font.size = 12
        ann.font.color = C['text']

    return fig


def win_loss_pie(trades: pd.DataFrame) -> go.Figure:
    wins   = int(trades["is_winner"].sum())
    losses = int((~trades["is_winner"]).sum())

    fig = go.Figure(go.Pie(
        labels=["Winners", "Losers"],
        values=[wins, losses],
        marker_colors=[C["green"], C["red"]],
        hole=0.55,
        textinfo="label+percent",
        hovertemplate="%{label}: %{value} trades (%{percent})<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor=C["surface"],
        font=dict(color=C["text"]),
        showlegend=False,
        margin=dict(l=20, r=20, t=40, b=20),
        height=260,
        title=dict(text="Win / Loss Split", font=dict(color=C["text"], size=14)),
    )
    return fig
