import time
import MetaTrader5 as mt5
from decouple import config, AutoConfig
from mt5.meter_trader_config import MetaTraderConfig
from strategies.claudestrategy import MonthlyTrendStrategy
from datetime import datetime, timezone
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


def sleep_until_next_15min():
    now = datetime.now(timezone.utc)
    seconds_past = (now.minute % 15) * 60 + now.second
    seconds_to_wait = (15 * 60) - seconds_past
    print(f"   ⏳ Next bar in {seconds_to_wait}s")
    time.sleep(seconds_to_wait)


reload_decouple()

LIVE    = False
SYMBOLS = ["EURUSDm", "USDJPYm"]


def main():

    # ── MT5 ──────────────────────────────────────────────────────────────
    mt5_config = MetaTraderConfig()
    mt5_settings = {
        "username":    config("MT5_USERNAME"      if LIVE else "MT5_USERNAME_TRIAL"),
        "password":    config("MT5_PASSWORD"      if LIVE else "MT5_PASSWORD_TRIAL"),
        "server":      config("MT5_SERVER"        if LIVE else "MT5_SERVER_TRIAL"),
        "mt5_pathway": config("MT5_PATHWAY"),
    }

    print(f"🎯 Mode: {'LIVE' if LIVE else 'DEMO'}")

    if not mt5_config.start_mt5(mt5_settings):
        print("❌ MT5 failed to start")
        return

    # ── Strategy ─────────────────────────────────────────────────────────
    strategy = MonthlyTrendStrategy(
        risk_pct=1.0,
        min_sl_pips=35.0,
        trail_trigger_r=1.0,
        trail_pips=20.0,
        initial_balance=100.0,
    )

    print(f"🧠 {strategy}\n")

    # restore any open positions surviving a restart
    strategy.restore_open_trades(SYMBOLS)

    # ── Main loop ─────────────────────────────────────────────────────────
    while True:
        now = datetime.now(timezone.utc)

        print("=" * 55)
        print(f"🕒 {now.strftime('%A %d %B %Y — %H:%M UTC')}")
        print("=" * 55)

        for symbol in SYMBOLS:
            print(f"\n📊 {symbol}")

            # reset monthly tracker on the 1st of each month
            if now.day == 1 and now.hour == 0:
                strategy.reset_month(symbol, now.month)

            open_trades = mt5_config.get_open_trades_count(symbol=symbol)

            # ── manage open trade ─────────────────────────────────────
            if open_trades > 0:
                status = strategy.manage_open_trade(symbol)
                print(f"   📌 {status}")

                # check if MT5 closed the trade via SL or trail
                if mt5_config.get_open_trades_count(symbol=symbol) == 0:
                    # determine result from most recent closed deal
                    deals = mt5.history_deals_get(
                        int(time.time()) - 86400,
                        int(time.time())
                    ) or []
                    own_deals = [
                        d for d in deals
                        if d.symbol == symbol
                        and d.entry == 1
                    ]
                    was_win = own_deals[-1].profit > 0 if own_deals else False
                    strategy.record_result(symbol, was_win, now.month)
                    print(f"   🏁 {'WIN ✅' if was_win else 'LOSS ❌'}")
                    print(f"   📊 {strategy.get_performance_summary(symbol)}")

                continue

            # ── look for new entry ────────────────────────────────────
            signal = strategy.generate_signal(symbol)
            print(f"   {signal['reason']}")

            if not signal["signal"]:
                continue

            print(f"   🎯 {signal['signal'].upper()} | "
                  f"Entry {signal['entry_price']} | "
                  f"SL {signal['stop_loss']} ({signal['sl_pips']}p) | "
                  f"Lot {signal['lot_size']}")

            success = mt5_config.execute_trade(
                symbol=symbol,
                signal=signal["signal"],
                stop_loss=signal["stop_loss"],
                take_profit=signal["take_profit"],  # 0 — no fixed TP
                lot_size=signal["lot_size"],
                strategy_name=strategy.__class__.__name__,
            )

            print("   ✅ Executed" if success else "   ❌ Failed")

        print("\n✅ Cycle done")
        sleep_until_next_15min()


if __name__ == "__main__":
    main()