"""Trade log for the gold straddle bot (gold_straddle_strategy.py).

Prints every closed trade for GOLD_0000 (magic 20261011) and GOLD_0100
(magic 20261012), then a short per-setup summary so the two times can be
compared side by side.

    python gold_straddle_tracker.py            # last 30 days
    python gold_straddle_tracker.py --days 90
"""
import argparse
import MetaTrader5 as mt5
from decouple import config, AutoConfig
from mt5.meter_trader_config import MetaTraderConfig
from datetime import datetime, timezone, timedelta
import os


def reload_decouple():
    KEYS = [
        "MT5_USERNAME", "MT5_PASSWORD", "MT5_SERVER",
        "MT5_USERNAME_TRIAL", "MT5_PASSWORD_TRIAL", "MT5_SERVER_TRIAL",
        "MT5_PATHWAY",
    ]
    for k in KEYS:
        os.environ.pop(k, None)
    AutoConfig._instances = {}


reload_decouple()

LIVE = True  # must match the account main_gold_straddle.py trades on
SETUPS = {20261011: "00:00", 20261012: "01:00"}  # magic -> setup

# getattr + documented MQL5 values, so a missing name can never crash this
DEAL_ENTRY_IN = getattr(mt5, "DEAL_ENTRY_IN", 0)
DEAL_ENTRY_OUT = getattr(mt5, "DEAL_ENTRY_OUT", 1)
DEAL_TYPE_BUY = getattr(mt5, "DEAL_TYPE_BUY", 0)
DEAL_REASON_SL = getattr(mt5, "DEAL_REASON_SL", 4)
DEAL_REASON_TP = getattr(mt5, "DEAL_REASON_TP", 5)


def ts(t):
    return datetime.fromtimestamp(t, tz=timezone.utc)


def costs(d):
    """Commission + swap + fee for a deal (usually negative)."""
    return d.commission + d.swap + getattr(d, "fee", 0.0)


def exit_type(out_deal, net):
    c = (out_deal.comment or "").lower()
    if c.endswith("_deadline"):
        return "deadline close"
    if c.endswith("_dual"):
        return "dual-fill close"
    if out_deal.reason == DEAL_REASON_SL:
        if net > 0.01:
            return "trail stop"
        if net >= -0.5:
            return "breakeven"
        return "stop loss"
    if out_deal.reason == DEAL_REASON_TP:
        return "take profit"
    return "manual/other"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    mt5_config = MetaTraderConfig()
    mt5_settings = {
        "username":    config("MT5_USERNAME"      if LIVE else "MT5_USERNAME_TRIAL"),
        "password":    config("MT5_PASSWORD"      if LIVE else "MT5_PASSWORD_TRIAL"),
        "server":      config("MT5_SERVER"        if LIVE else "MT5_SERVER_TRIAL"),
        "mt5_pathway": config("MT5_PATHWAY"),
    }
    print(f"Mode: {'LIVE' if LIVE else 'DEMO'}")
    if not mt5_config.start_mt5(mt5_settings):
        print("MT5 failed to start")
        return

    # no group= parameter (returned nothing on this account) — filter in Python
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=args.days)
    deals = [d for d in (mt5.history_deals_get(since, now) or ()) if d.magic in SETUPS]

    # pair each position's entry and exit deals via position_id
    by_pos = {}
    for d in deals:
        p = by_pos.setdefault(d.position_id, {"in": None, "out": [], "costs": 0.0})
        p["costs"] += costs(d)
        if d.entry == DEAL_ENTRY_IN:
            p["in"] = d
        elif d.entry == DEAL_ENTRY_OUT:
            p["out"].append(d)

    trades = []
    for pos_id, p in by_pos.items():
        if p["in"] is None or not p["out"]:
            continue  # still open, or entry older than --days
        din, dout = p["in"], max(p["out"], key=lambda x: x.time)
        gross = sum(x.profit for x in p["out"])
        net = gross + p["costs"]
        hold = ts(dout.time) - ts(din.time)
        trades.append({
            "setup": SETUPS[din.magic],
            "open": ts(din.time),
            "side": "BUY" if din.type == DEAL_TYPE_BUY else "SELL",
            "lots": din.volume,
            "entry": din.price,
            "close": ts(dout.time),
            "exit": dout.price,
            "move": (dout.price - din.price) * (1 if din.type == DEAL_TYPE_BUY else -1),
            "gross": gross,
            "net": net,
            "hold": hold,
            "how": exit_type(dout, net),
            "pos": pos_id,
        })
    trades.sort(key=lambda t: t["open"])

    # ---- trade log ----
    print(f"\nGOLD STRADDLE — closed trades, last {args.days} days (times UTC)")
    print(f"{'Setup':6s} {'Opened':16s} {'Side':4s} {'Lots':>5s} {'Entry':>9s} "
          f"{'Closed':16s} {'Exit':>9s} {'Move $':>7s} {'Net $':>8s} {'Held':>7s}  {'Result':6s} How")
    print("-" * 118)
    last_day = None
    for t in trades:
        day = t["open"].date()
        if last_day is not None and day != last_day:
            print()
        last_day = day
        res = "WIN" if t["net"] > 0.01 else ("LOSS" if t["net"] < -0.01 else "B/E")
        h, m = divmod(int(t["hold"].total_seconds()) // 60, 60)
        print(f"{t['setup']:6s} {t['open']:%a %d %b %H:%M} {t['side']:4s} {t['lots']:>5.2f} "
              f"{t['entry']:>9.2f} {t['close']:%a %d %b %H:%M} {t['exit']:>9.2f} "
              f"{t['move']:>+7.2f} {t['net']:>+8.2f} {h:>3d}h{m:02d}m  {res:6s} {t['how']}")

    open_pos = [p for p in (mt5.positions_get(symbol="XAUUSDm") or ()) if p.magic in SETUPS]
    for p in open_pos:
        side = "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"
        print(f"{SETUPS[p.magic]:6s} {ts(p.time):%a %d %b %H:%M} {side:4s} {p.volume:>5.2f} "
              f"{p.price_open:>9.2f}   ** STILL OPEN **  SL {p.sl}  floating {p.profit:+.2f}")

    # ---- per-month profit ----
    print(f"\n{'=' * 60}\nBY MONTH (net $, trades in brackets)")
    print(f"  {'Month':8s} {'00:00':>16s} {'01:00':>16s} {'Both':>16s} {'Win%':>6s} {'Running':>11s}")
    months = {}
    for t in trades:
        m = months.setdefault(t["open"].strftime("%Y-%m"),
                              {"00:00": [0.0, 0], "01:00": [0.0, 0], "wins": 0})
        m[t["setup"]][0] += t["net"]
        m[t["setup"]][1] += 1
        m["wins"] += t["net"] > 0.01
    running = 0.0
    for month in sorted(months):
        m = months[month]
        both = m["00:00"][0] + m["01:00"][0]
        n = m["00:00"][1] + m["01:00"][1]
        running += both
        print(f"  {month:8s} "
              f"{m['00:00'][0]:>+10,.2f} ({m['00:00'][1]:>2d}) "
              f"{m['01:00'][0]:>+10,.2f} ({m['01:00'][1]:>2d}) "
              f"{both:>+10,.2f} ({n:>2d}) {m['wins'] / n * 100:>5.0f}% {running:>+11,.2f}")
    if months:
        nets = {k: v["00:00"][0] + v["01:00"][0] for k, v in months.items()}
        best = max(nets.items(), key=lambda kv: kv[1])
        worst = min(nets.items(), key=lambda kv: kv[1])
        print(f"\n  Best month:  {best[0]}  ${best[1]:,.2f}")
        print(f"  Worst month: {worst[0]}  ${worst[1]:,.2f}")
        print(f"  Profitable months: {sum(v > 0 for v in nets.values())}/{len(nets)}")
        print(f"  Avg net per month: ${sum(nets.values()) / len(nets):,.2f}")

    # ---- per-setup summary ----
    print(f"\n{'=' * 60}\nSUMMARY (net of commission/swap)")
    print(f"  {'':16s} {'00:00':>12s} {'01:00':>12s} {'Both':>12s}")
    groups = {
        "00:00": [t for t in trades if t["setup"] == "00:00"],
        "01:00": [t for t in trades if t["setup"] == "01:00"],
        "Both": trades,
    }

    def row(label, fn):
        print(f"  {label:16s} " + " ".join(f"{fn(g):>12s}" for g in groups.values()))

    row("Trades", lambda g: str(len(g)))
    row("Wins", lambda g: str(sum(t["net"] > 0.01 for t in g)))
    row("Losses", lambda g: str(sum(t["net"] < -0.01 for t in g)))
    row("Win rate", lambda g: f"{sum(t['net'] > 0.01 for t in g) / len(g) * 100:.1f}%" if g else "-")
    row("Net P&L", lambda g: f"${sum(t['net'] for t in g):,.2f}")
    row("Avg win", lambda g: (lambda w: f"${sum(w) / len(w):,.2f}" if w else "-")(
        [t["net"] for t in g if t["net"] > 0.01]))
    row("Avg loss", lambda g: (lambda l: f"${sum(l) / len(l):,.2f}" if l else "-")(
        [t["net"] for t in g if t["net"] < -0.01]))
    row("Best trade", lambda g: f"${max(t['net'] for t in g):,.2f}" if g else "-")
    row("Worst trade", lambda g: f"${min(t['net'] for t in g):,.2f}" if g else "-")
    row("Move $ total", lambda g: f"{sum(t['move'] for t in g):+,.2f}")
    print("  (Move $ = gold price move captured, per oz — compares the two times"
          " fairly even if lot sizes differ)")

    mt5.shutdown()


if __name__ == "__main__":
    main()