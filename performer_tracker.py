# performance_tracker.py
"""
Performance tracker — ALL strategies, one Excel file (read-only)
================================================================
Reads the account's deal history from MT5 and writes one workbook:
  Summary   : per strategy — trades, wins, win %, net $, avg win / loss,
              profit factor, max drawdown, best / worst trade, open now,
              and the backtest win rate next to the live one.
  Monthly   : net $ per strategy per month (green > 0, red < 0).
  Trades    : every closed trade, newest first (win rows green, loss rows red).
  Open      : positions open right now, per strategy.
Never places, changes or closes anything — only reads history.

Run:   python performance_tracker.py                 (live account, since FROM_DATE)
       python performance_tracker.py --demo          (demo account)
       python performance_tracker.py --since 2026-11-01
Needs: pip install openpyxl
"""

from __future__ import annotations

import argparse
import datetime
import os
from typing import Any, Dict, List

from mt5.meter_trader_config import MetaTraderConfig   # same login helper the bots use
import MetaTrader5 as mt5
from decouple import config, AutoConfig
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# ---------------------------------------------------------------------------
# Strategies by magic. "bt_win" = backtest win rate (register), for comparison.
# ---------------------------------------------------------------------------
STRATEGIES: Dict[int, Dict[str, Any]] = {
    20261011: {"name": "1. Gold straddle 00:00", "group": "1. Gold straddle", "bt_win": 54},
    20261012: {"name": "1. Gold straddle 01:00", "group": "1. Gold straddle", "bt_win": 54},
    20261028: {"name": "2. Dip buy US30", "group": "2. Index dip buy", "bt_win": None},
    20261029: {"name": "2. Dip buy USTEC", "group": "2. Index dip buy", "bt_win": None},
    20261034: {"name": "3. Pre-FOMC US30", "group": "3. Pre-FOMC", "bt_win": 71},
    20261035: {"name": "3. Pre-FOMC USTEC", "group": "3. Pre-FOMC", "bt_win": 67},
    20261036: {"name": "3. Pre-FOMC US500", "group": "3. Pre-FOMC", "bt_win": 68},
    20260807: {"name": "4. News straddle gold", "group": "4. News straddle", "bt_win": 74},
    20261037: {"name": "5. Gold hat long", "group": "5. Gold hat long", "bt_win": 56},
    20261038: {"name": "6. USDJPY Asian range", "group": "6. USDJPY Asian range", "bt_win": 51},
}
FROM_DATE = datetime.date(2027, 1, 1)     # live start of the January 2027 plan
OUT_DIR = "reports"

GREEN = PatternFill("solid", fgColor="C6EFCE")
RED = PatternFill("solid", fgColor="FFC7CE")
HEAD = PatternFill("solid", fgColor="1F3864")
BOLD = Font(bold=True)
WHITE = Font(bold=True, color="FFFFFF")


def _ts(seconds: float) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# MT5
# ---------------------------------------------------------------------------
def connect(demo: bool) -> bool:
    for k in ("MT5_USERNAME", "MT5_PASSWORD", "MT5_SERVER", "MT5_USERNAME_TRIAL",
              "MT5_PASSWORD_TRIAL", "MT5_SERVER_TRIAL", "MT5_PATHWAY"):
        os.environ.pop(k, None)
    AutoConfig._instances = {}
    sfx = "_TRIAL" if demo else ""
    ok = MetaTraderConfig().start_mt5({
        "username": config("MT5_USERNAME" + sfx),
        "password": config("MT5_PASSWORD" + sfx),
        "server": config("MT5_SERVER" + sfx),
        "mt5_pathway": config("MT5_PATHWAY"),
    })
    if not ok:
        print(f"MT5 failed to start: {mt5.last_error()}")
    return bool(ok)


def read_deals(since: datetime.datetime, until: datetime.datetime):
    """Deal history, read twice until two reads agree (half-synced history guard)."""
    prev = None
    for _ in range(4):
        cur = mt5.history_deals_get(since, until)
        if cur is not None and prev is not None and len(cur) == len(prev):
            return cur
        prev = cur
    return prev


def closed_trades(deals) -> List[Dict[str, Any]]:
    """One row per CLOSED position of a known strategy. Positions are found by
    the magic on their deals; every deal of the position is summed (an SL exit
    is a server deal and may not carry the magic)."""
    owner: Dict[int, int] = {}
    for d in deals:
        if d.position_id and d.magic in STRATEGIES and d.position_id not in owner:
            owner[d.position_id] = d.magic
    by_pos: Dict[int, List[Any]] = {}
    for d in deals:
        if d.position_id in owner:
            by_pos.setdefault(d.position_id, []).append(d)
    open_ids = {p.ticket for p in (mt5.positions_get() or ())}
    rows = []
    for pid, ds in by_pos.items():
        if pid in open_ids:
            continue
        ds.sort(key=lambda x: (x.time_msc, x.ticket))
        ins = [x for x in ds if x.entry == mt5.DEAL_ENTRY_IN]
        outs = [x for x in ds if x.entry != mt5.DEAL_ENTRY_IN]
        if not ins or not outs:
            continue                            # opening deal outside the window, or not closed yet
        first, last = ins[0], outs[-1]
        vol = sum(x.volume for x in ins)
        net = sum(x.profit + x.swap + x.commission + getattr(x, "fee", 0.0) for x in ds)
        s = STRATEGIES[owner[pid]]
        rows.append({
            "strategy": s["name"], "group": s["group"], "magic": owner[pid],
            "symbol": first.symbol, "side": "BUY" if first.type == mt5.DEAL_TYPE_BUY else "SELL",
            "volume": round(vol, 2), "open_time": _ts(first.time), "close_time": _ts(last.time),
            "open_price": first.price, "close_price": last.price,
            "net": round(net, 2), "per_001": round(net * 0.01 / vol, 2) if vol else 0.0,
            "exit": (last.comment or "")[:20], "position": pid,
        })
    rows.sort(key=lambda r: r["close_time"], reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(trades)
    wins = [t["net"] for t in trades if t["net"] > 0]
    losses = [t["net"] for t in trades if t["net"] <= 0]
    eq, peak, dd = 0.0, 0.0, 0.0
    for t in sorted(trades, key=lambda r: r["close_time"]):
        eq += t["net"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    gl = -sum(losses)
    return {
        "trades": n, "wins": len(wins),
        "win_pct": round(len(wins) / n * 100, 1) if n else None,
        "net": round(sum(t["net"] for t in trades), 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "pf": round(sum(wins) / gl, 2) if gl > 0 else None,
        "max_dd": round(dd, 2),
        "best": max((t["net"] for t in trades), default=None),
        "worst": min((t["net"] for t in trades), default=None),
    }


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------
def _header(ws, cols: List[str]) -> None:
    ws.append(cols)
    for c in ws[ws.max_row]:
        c.fill, c.font = HEAD, WHITE
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.freeze_panes = ws.cell(row=ws.max_row + 1, column=1)


def _widths(ws, widths: List[int]) -> None:
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _money(cell) -> None:
    cell.number_format = '#,##0.00;-#,##0.00;0.00'


def build_workbook(trades, open_pos, since, until, account) -> Workbook:
    wb = Workbook()

    # --- Summary
    ws = wb.active
    ws.title = "Summary"
    ws.append([f"Strategy performance — account {account}"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"Closed trades from {since:%d %b %Y} to {until:%d %b %Y %H:%M} UTC (net = profit + swap + commission)"])
    ws.append([])
    _header(ws, ["Strategy", "Trades", "Wins", "Win %", "Backtest win %", "Net $", "Avg win $",
                 "Avg loss $", "Profit factor", "Max drawdown $", "Best $", "Worst $", "Open now"])
    names = list(dict.fromkeys(s["name"] for s in STRATEGIES.values()))
    groups = list(dict.fromkeys(s["group"] for s in STRATEGIES.values()))
    open_count: Dict[str, int] = {}
    for p in open_pos:
        if p.magic in STRATEGIES:
            nm = STRATEGIES[p.magic]["name"]
            open_count[nm] = open_count.get(nm, 0) + 1

    def row(label, ts, bt, n_open, bold=False):
        st = stats(ts)
        ws.append([label, st["trades"], st["wins"], st["win_pct"], bt, st["net"], st["avg_win"],
                   st["avg_loss"], st["pf"], st["max_dd"], st["best"], st["worst"], n_open])
        r = ws.max_row
        for col in (6, 7, 8, 10, 11, 12):
            _money(ws.cell(r, col))
        net = ws.cell(r, 6)
        if st["trades"]:
            net.fill = GREEN if st["net"] > 0 else RED
        if bold:
            for c in ws[r]:
                c.font = BOLD

    for g in groups:
        members = [n for n in names if any(s["name"] == n and s["group"] == g for s in STRATEGIES.values())]
        if len(members) > 1:
            for m in members:
                bt = next(s["bt_win"] for s in STRATEGIES.values() if s["name"] == m)
                row("   " + m, [t for t in trades if t["strategy"] == m], bt, open_count.get(m, 0))
        bt = next(s["bt_win"] for s in STRATEGIES.values() if s["group"] == g)
        row(g + (" (total)" if len(members) > 1 else ""), [t for t in trades if t["group"] == g],
            bt, sum(open_count.get(m, 0) for m in members), bold=True)
    ws.append([])
    row("ALL STRATEGIES", trades, None, sum(open_count.values()), bold=True)
    _widths(ws, [30, 8, 7, 8, 10, 12, 11, 11, 10, 12, 10, 10, 9])

    # --- Monthly
    wm = wb.create_sheet("Monthly")
    months = sorted({t["close_time"].strftime("%Y-%m") for t in trades})
    _header(wm, ["Strategy"] + months + ["Total"])
    for g in groups + ["ALL"]:
        ts = trades if g == "ALL" else [t for t in trades if t["group"] == g]
        vals = [round(sum(t["net"] for t in ts if t["close_time"].strftime("%Y-%m") == m), 2) for m in months]
        wm.append([g] + vals + [round(sum(vals), 2)])
        r = wm.max_row
        for i, v in enumerate(vals + [sum(vals)], 2):
            c = wm.cell(r, i)
            _money(c)
            if v > 0:
                c.fill = GREEN
            elif v < 0:
                c.fill = RED
        if g == "ALL":
            for c in wm[r]:
                c.font = BOLD
    _widths(wm, [26] + [11] * (len(months) + 1))

    # --- Trades
    wt = wb.create_sheet("Trades")
    _header(wt, ["Closed (UTC)", "Opened (UTC)", "Strategy", "Symbol", "Side", "Lots",
                 "Open price", "Close price", "Net $", "Net per 0.01 lot", "Exit comment", "Position"])
    for t in trades:
        wt.append([t["close_time"].replace(tzinfo=None), t["open_time"].replace(tzinfo=None), t["strategy"],
                   t["symbol"], t["side"], t["volume"], t["open_price"], t["close_price"],
                   t["net"], t["per_001"], t["exit"], t["position"]])
        r = wt.max_row
        fill = GREEN if t["net"] > 0 else RED
        for c in wt[r]:
            c.fill = fill
        wt.cell(r, 1).number_format = wt.cell(r, 2).number_format = "yyyy-mm-dd hh:mm"
        _money(wt.cell(r, 9))
        _money(wt.cell(r, 10))
    _widths(wt, [17, 17, 26, 9, 6, 6, 11, 11, 10, 12, 18, 13])

    # --- Open
    wo = wb.create_sheet("Open")
    _header(wo, ["Strategy", "Symbol", "Side", "Lots", "Opened (UTC)", "Open price", "Stop", "Floating $"])
    for p in sorted(open_pos, key=lambda x: x.time):
        if p.magic not in STRATEGIES:
            continue
        wo.append([STRATEGIES[p.magic]["name"], p.symbol, "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
                   p.volume, _ts(p.time).replace(tzinfo=None), p.price_open, p.sl or None,
                   round(p.profit + p.swap, 2)])
        r = wo.max_row
        wo.cell(r, 5).number_format = "yyyy-mm-dd hh:mm"
        _money(wo.cell(r, 8))
        wo.cell(r, 8).fill = GREEN if p.profit + p.swap > 0 else RED
    _widths(wo, [26, 9, 6, 6, 17, 11, 11, 11])
    return wb


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="All-strategy performance tracker (read-only)")
    ap.add_argument("--demo", action="store_true", help="read the demo account instead of live")
    ap.add_argument("--since", help="start date YYYY-MM-DD (default %s)" % FROM_DATE)
    args = ap.parse_args()
    since_d = datetime.date.fromisoformat(args.since) if args.since else FROM_DATE

    if not connect(args.demo):
        return
    try:
        acc = mt5.account_info()
        account = f"{acc.login} ({'DEMO' if args.demo else 'LIVE'})" if acc else ("DEMO" if args.demo else "LIVE")
        since = datetime.datetime.combine(since_d - datetime.timedelta(days=3), datetime.time(),
                                          tzinfo=datetime.timezone.utc)   # 3 days back: catch opens of early trades
        until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=1)
        deals = read_deals(since, until)
        if deals is None:
            print(f"Could not read deal history: {mt5.last_error()}")
            return
        trades = [t for t in closed_trades(deals) if t["close_time"].date() >= since_d]
        open_pos = list(mt5.positions_get() or ())
    finally:
        mt5.shutdown()

    wb = build_workbook(trades, open_pos, since_d, until, account)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"strategy_performance_{'demo' if args.demo else 'live'}_"
                                 f"{until:%Y-%m-%d_%H%M}.xlsx")
    wb.save(path)

    print(f"Saved {path}")
    print(f"{'Strategy':<26}{'Trades':>7}{'Win %':>8}{'Net $':>11}")
    for g in dict.fromkeys(s["group"] for s in STRATEGIES.values()):
        st = stats([t for t in trades if t["group"] == g])
        print(f"{g:<26}{st['trades']:>7}{(str(st['win_pct']) if st['win_pct'] is not None else '-'):>8}{st['net']:>11.2f}")
    st = stats(trades)
    print(f"{'ALL':<26}{st['trades']:>7}{(str(st['win_pct']) if st['win_pct'] is not None else '-'):>8}{st['net']:>11.2f}")


if __name__ == "__main__":
    main()