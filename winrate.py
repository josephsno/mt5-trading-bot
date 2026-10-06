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

LIVE = True
STRADDLE_MAGIC = 20260716  # straddle_strategy.py's own MAGIC constant


def costs(d):
    """Commission + swap + fee for a deal (usually negative)."""
    return d.commission + d.swap + getattr(d, "fee", 0.0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=365)
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

    # ---- no group= parameter, filter in Python instead ----
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    now = datetime.now(timezone.utc)
    deals = mt5.history_deals_get(since, now) or ()

    straddle_deals = [d for d in deals if d.magic == STRADDLE_MAGIC]
    straddle_closes = [d for d in straddle_deals if d.entry == mt5.DEAL_ENTRY_OUT]

    print(f"\nSTRADDLE STRATEGY (magic={STRADDLE_MAGIC}) — closed trades, last {args.days} days")
    print(f"Total: {len(straddle_closes)}\n")

    wins = 0
    losses = 0
    total_pnl = 0.0

    for d in sorted(straddle_closes, key=lambda x: x.time):
        outcome = "WIN " if d.profit > 0 else ("LOSS" if d.profit < 0 else "B/E ")
        if d.profit > 0:
            wins += 1
        elif d.profit < 0:
            losses += 1
        total_pnl += d.profit
        t = datetime.fromtimestamp(d.time, tz=timezone.utc)
        print(f"  {t}  {d.symbol:10s}  {outcome}  profit={d.profit:>8.2f}  comment='{d.comment}'")

    total_costs = sum(costs(d) for d in straddle_deals)

    print(f"\n{'='*60}")
    print(f"OVERALL")
    print(f"Total trades: {len(straddle_closes)}")
    print(f"Wins: {wins}   Losses: {losses}   Break-even: {len(straddle_closes)-wins-losses}")
    if straddle_closes:
        print(f"Win rate: {wins/len(straddle_closes)*100:.1f}%")
    print(f"Gross P&L: ${total_pnl:,.2f}")
    print(f"Costs (commission/swap/fee): ${total_costs:,.2f}")
    print(f"Net P&L: ${total_pnl + total_costs:,.2f}")

    # ---- per-month breakdown ----
    by_month = {}
    for d in straddle_deals:
        key = datetime.fromtimestamp(d.time, tz=timezone.utc).strftime("%Y-%m")
        m = by_month.setdefault(key, {"trades": 0, "wins": 0, "losses": 0,
                                      "gross": 0.0, "costs": 0.0})
        m["costs"] += costs(d)
        if d.entry == mt5.DEAL_ENTRY_OUT:
            m["trades"] += 1
            m["gross"] += d.profit
            if d.profit > 0:
                m["wins"] += 1
            elif d.profit < 0:
                m["losses"] += 1

    print(f"\n{'='*60}")
    print("BY MONTH")
    print(f"  {'Month':8s} {'Trades':>6s} {'W':>4s} {'L':>4s} {'Win%':>6s} "
          f"{'Gross':>11s} {'Costs':>10s} {'Net':>11s} {'Running':>11s}")
    running = 0.0
    for month in sorted(by_month):
        m = by_month[month]
        net = m["gross"] + m["costs"]
        running += net
        win_pct = m["wins"] / m["trades"] * 100 if m["trades"] else 0.0
        print(f"  {month:8s} {m['trades']:>6d} {m['wins']:>4d} {m['losses']:>4d} {win_pct:>5.1f}% "
              f"{m['gross']:>11,.2f} {m['costs']:>10,.2f} {net:>11,.2f} {running:>11,.2f}")

    if by_month:
        nets = {k: v["gross"] + v["costs"] for k, v in by_month.items()}
        best_month = max(nets.items(), key=lambda kv: kv[1])
        worst_month = min(nets.items(), key=lambda kv: kv[1])
        green = sum(1 for v in nets.values() if v > 0)
        print(f"\n  Best month:  {best_month[0]}  ${best_month[1]:,.2f}")
        print(f"  Worst month: {worst_month[0]}  ${worst_month[1]:,.2f}")
        print(f"  Profitable months: {green}/{len(nets)}")
        print(f"  Avg net per month: ${sum(nets.values()) / len(nets):,.2f}")

    # ---- per-month, per-symbol breakdown ----
    month_sym = {}
    for d in straddle_deals:
        month = datetime.fromtimestamp(d.time, tz=timezone.utc).strftime("%Y-%m")
        s = month_sym.setdefault(month, {}).setdefault(
            d.symbol, {"trades": 0, "wins": 0, "net": 0.0})
        s["net"] += costs(d)
        if d.entry == mt5.DEAL_ENTRY_OUT:
            s["trades"] += 1
            s["net"] += d.profit
            if d.profit > 0:
                s["wins"] += 1

    print(f"\n{'='*60}")
    print("SYMBOL RANKING PER MONTH (best → worst)")
    for month in sorted(month_sym):
        ranked = sorted(month_sym[month].items(), key=lambda kv: kv[1]["net"], reverse=True)
        print(f"\n  {month}")
        for i, (sym, s) in enumerate(ranked, 1):
            print(f"    {i:>2}. {sym:12s} ${s['net']:>9,.2f}  ({s['trades']} trades)")

    # ---- per-symbol breakdown ----
    by_symbol = {}
    for d in straddle_closes:
        by_symbol.setdefault(d.symbol, []).append(d)

    print(f"\n{'='*60}")
    print("BY SYMBOL")
    per_symbol_stats = {}
    for sym in sorted(by_symbol):
        trades = by_symbol[sym]
        sym_wins = sum(1 for d in trades if d.profit > 0)
        sym_losses = sum(1 for d in trades if d.profit < 0)
        sym_be = len(trades) - sym_wins - sym_losses
        sym_pnl = sum(d.profit for d in trades)
        sym_win_rate = sym_wins / len(trades) * 100 if trades else 0.0
        per_symbol_stats[sym] = {
            "trades": len(trades),
            "win_rate": sym_win_rate,
            "pnl": sym_pnl,
        }
        print(f"\n  {sym}")
        print(f"    Trades: {len(trades)}  (wins={sym_wins} losses={sym_losses} breakeven={sym_be})")
        print(f"    Win rate: {sym_win_rate:.1f}%")
        print(f"    P&L: ${sym_pnl:,.2f}")

    # ---- general summary ----
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"Symbols traded: {len(per_symbol_stats)}")
    if straddle_closes:
        avg_trades = len(straddle_closes) / len(per_symbol_stats)
        print(f"Avg trades per symbol: {avg_trades:.1f}")

    if per_symbol_stats:
        best_win_rate = max(per_symbol_stats.items(), key=lambda kv: kv[1]["win_rate"])
        worst_win_rate = min(per_symbol_stats.items(), key=lambda kv: kv[1]["win_rate"])
        best_pnl = max(per_symbol_stats.items(), key=lambda kv: kv[1]["pnl"])
        worst_pnl = min(per_symbol_stats.items(), key=lambda kv: kv[1]["pnl"])

        print(f"\nBest win rate:  {best_win_rate[0]:10s} {best_win_rate[1]['win_rate']:.1f}%  "
              f"({best_win_rate[1]['trades']} trades)")
        print(f"Worst win rate: {worst_win_rate[0]:10s} {worst_win_rate[1]['win_rate']:.1f}%  "
              f"({worst_win_rate[1]['trades']} trades)")
        print(f"Best P&L:       {best_pnl[0]:10s} ${best_pnl[1]['pnl']:,.2f}")
        print(f"Worst P&L:      {worst_pnl[0]:10s} ${worst_pnl[1]['pnl']:,.2f}")

        profitable = [s for s, v in per_symbol_stats.items() if v["pnl"] > 0]
        losing = [s for s, v in per_symbol_stats.items() if v["pnl"] < 0]
        print(f"\nProfitable symbols ({len(profitable)}): {', '.join(sorted(profitable)) or 'none'}")
        print(f"Losing symbols ({len(losing)}): {', '.join(sorted(losing)) or 'none'}")

    mt5.shutdown()


if __name__ == "__main__":
    main()