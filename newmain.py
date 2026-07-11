import time
import MetaTrader5 as mt5
from decouple import config, AutoConfig
from mt5.meter_trader_config import MetaTraderConfig
from strategies.monthlyclaude import MonthlyCandleTrailStrategy
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


def sleep_until_next_check(hours: int = 24):
    """
    This strategy only acts on month boundaries (entries) and daily trail
    checks -- no need for the M15 bot's 15-minute cadence. Checking once a
    day is already far more often than the strategy can act on; this just
    avoids hammering MT5 for no reason.
    """
    print(f"   ⏳ Next check in {hours}h")
    time.sleep(hours * 60 * 60)


reload_decouple()

LIVE    = False
SYMBOLS = ["EURUSDm", "USDJPYm", "GBPUSDm"]


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
    strategy = MonthlyCandleTrailStrategy(
        risk_pct=1.0,
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

            open_trades = mt5_config.get_open_trades_count(symbol=symbol)

            # ── manage open trade (trail update, only acts on month change) ──
            if open_trades > 0:
                status = strategy.manage_open_trade(symbol)
                print(f"   📌 {status}")

                # check if MT5 closed the trade via SL
                if mt5_config.get_open_trades_count(symbol=symbol) == 0:
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
                    strategy.record_result(symbol, was_win)
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
        sleep_until_next_check(hours=24)


if __name__ == "__main__":
    main()