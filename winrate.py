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

    # ---- THE FIX: no group= parameter, filter in Python instead ----
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    now = datetime.now(timezone.utc)
    deals = mt5.history_deals_get(since, now) or ()

    straddle_closes = [
        d for d in deals
        if d.magic == STRADDLE_MAGIC and d.entry == mt5.DEAL_ENTRY_OUT
    ]

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

    print(f"\n{'='*60}")
    print(f"OVERALL")
    print(f"Total trades: {len(straddle_closes)}")
    print(f"Wins: {wins}   Losses: {losses}   Break-even: {len(straddle_closes)-wins-losses}")
    if straddle_closes:
        print(f"Win rate: {wins/len(straddle_closes)*100:.1f}%")
    print(f"Total P&L: ${total_pnl:,.2f}")

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