import time
import MetaTrader5 as mt5
from decouple import config, AutoConfig
from mt5.meter_trader_config import MetaTraderConfig
from strategies.straddlestrategy import StraddleStrategy
from datetime import datetime, timezone
import os


def reload_decouple():
    KEYS = [
        "MT5_USERNAME",
        "MT5_PASSWORD",
        "MT5_SERVER",
        "MT5_USERNAME_TRIAL",
        "MT5_PASSWORD_TRIAL",
        "MT5_SERVER_TRIAL",
        "MT5_PATHWAY",
    ]
    for k in KEYS:
        os.environ.pop(k, None)
    AutoConfig._instances = {}


def sleep_until_next_5min():
    now = datetime.now(timezone.utc)
    seconds_past = (now.minute % 5) * 60 + now.second
    seconds_to_wait = (5 * 60) - seconds_past
    time.sleep(seconds_to_wait)


reload_decouple()

LIVE = True

# SYMBOLS is derived from the strategy itself (traded_symbols), not
# hardcoded here — this keeps GOLD_ENABLED as the single source of truth
# for which symbols are actually live.


def main():

    # ── MT5 ──────────────────────────────────────────────────────────────
    mt5_config = MetaTraderConfig()
    mt5_settings = {
        "username": config("MT5_USERNAME" if LIVE else "MT5_USERNAME_TRIAL"),
        "password": config("MT5_PASSWORD" if LIVE else "MT5_PASSWORD_TRIAL"),
        "server": config("MT5_SERVER" if LIVE else "MT5_SERVER_TRIAL"),
        "mt5_pathway": config("MT5_PATHWAY"),
    }

    print(f"Mode: {'LIVE' if LIVE else 'DEMO'}")

    if not mt5_config.start_mt5(mt5_settings):
        print("MT5 failed to start")
        return

    # ── Strategy ─────────────────────────────────────────────────────────
    strategy = StraddleStrategy(
        risk_pct=1.0,
        initial_balance=90.0,
    )

    print(f"{strategy}\n")

    # No restore_open_trades() call needed — the strategy is stateless and
    # reads positions/pending orders straight from MT5 on every call, so
    # there is nothing to rebuild after a restart.

    # ── Main loop ─────────────────────────────────────────────────────────
    while True:
        now = datetime.now(timezone.utc)

        print("=" * 55)
        print(f"{now.strftime('%A %d %B %Y — %H:%M UTC')}")
        print("=" * 55)

        for symbol in strategy.traded_symbols:
            print(f"\n{symbol}")

            open_trades = mt5_config.get_open_trades_count(symbol=symbol)

            # ── manage a filled position ──────────────────────────────
            if open_trades > 0:
                status = strategy.manage_open_trade(symbol)
                print(f"   {status}")

                # detect the trade closing this cycle (SL, trail, or the
                # adaptive deadline) — read the outcome straight from MT5 history
                # rather than tracking it ourselves
                if mt5_config.get_open_trades_count(symbol=symbol) == 0:
                    deals = (
                        mt5.history_deals_get(
                            int(time.time()) - 86400, int(time.time())
                        )
                        or []
                    )
                    own_deals = [
                        d
                        for d in deals
                        if d.symbol == symbol and d.entry == mt5.DEAL_ENTRY_OUT
                    ]
                    was_win = own_deals[-1].profit > 0 if own_deals else False
                    print(f"   {'WIN' if was_win else 'LOSS'}")
                    print(f"   {strategy.get_performance_summary(symbol)}")

                continue

            # ── manage a still-pending straddle (before either side fills) ──
            pending_status = strategy.manage_pending_orders(symbol)
            if pending_status != "No pending straddle":
                print(f"   {pending_status}")
                continue

            # ── look for a new entry ───────────────────────────────────
            signal = strategy.check_and_place(symbol)
            print(f"   {signal['reason']}")

            # check_and_place already sends both orders itself when
            # conditions are met — a straddle is two linked orders placed
            # atomically, so there's nothing further to execute here.

        print("\nCycle done")
        sleep_until_next_5min()


if __name__ == "__main__":
    main()
