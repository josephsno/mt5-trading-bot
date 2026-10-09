import time
from decouple import config, AutoConfig
from mt5.meter_trader_config import MetaTraderConfig
from strategies.gold_straddle_strategy import GoldStraddleStrategy, _utc_now
from datetime import datetime
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


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------
# 5-minute clock-aligned polling (:00, :05, :10 ...) — exactly what the
# backtest assumed. The 00:00 and 01:00 polls are the entry polls; every
# other poll manages BE / trail / OCO / deadline. Faster polling was tested
# (1s, 10s, 30s, 60s) and was not reliably better — keep 5 min.
POLL_SECONDS = 300


def sleep_until_next_poll(now: datetime, interval: int = POLL_SECONDS):
    """Sleep until the next interval-aligned mark (e.g. 00:05:00), not just
    'interval seconds from now' — keeps cycles on the clock, no drift."""
    seconds_into = (now.minute * 60 + now.second) % interval
    seconds_to_wait = interval - seconds_into - now.microsecond / 1_000_000
    if seconds_to_wait <= 0:
        seconds_to_wait += interval
    time.sleep(seconds_to_wait)


reload_decouple()

LIVE = False  # 00:00 is new — demo first. See gold_straddle_strategy.py.

# Standalone process. GOLD_0000 = magic 20261011, GOLD_0100 = magic 20261012.
# Replaces the XAUUSDm part of straddle_strategy.py (magic 20260716) — do not
# trade gold in both. Only ever touches its own orders/positions.


def main():

    # ── MT5 ──────────────────────────────────────────────────────────────
    mt5_config = MetaTraderConfig()
    mt5_settings = {
        "username": config("MT5_USERNAME" if LIVE else "MT5_USERNAME_TRIAL"),
        "password": config("MT5_PASSWORD" if LIVE else "MT5_PASSWORD_TRIAL"),
        "server": config("MT5_SERVER" if LIVE else "MT5_SERVER_TRIAL"),
        "mt5_pathway": config("MT5_PATHWAY"),
    }

    print(
        f"Mode: {'LIVE' if LIVE else 'DEMO'} | "
        f"account {mt5_settings['username']} on {mt5_settings['server']}"
    )

    if not mt5_config.start_mt5(mt5_settings):
        print("MT5 failed to start")
        return

    # ── Strategy ─────────────────────────────────────────────────────────
    strategy = GoldStraddleStrategy()
    print(f"{strategy}\n")

    # ── Main loop ─────────────────────────────────────────────────────────
    while True:
        # ONE timestamp per cycle, in broker time (VPS clock corrected
        # against the XAUUSDm tick — the VPS was once seen 64s off).
        now = _utc_now()

        print("=" * 55)
        print(f"{now.strftime('%A %d %B %Y — %H:%M:%S UTC')}")
        print("=" * 55)

        for s in strategy.setups:
            print(f"\n{s['name']} ({s['symbol']} {s['trigger_hour']:02d}:{s['trigger_minute']:02d} UTC)")
            try:
                # Management FIRST, every cycle, unconditionally (OCO-ordering
                # bug lesson): OCO/expiry cancel, dual-fill heal, deadline
                # close, BE, trail.
                for msg in strategy.manage(s, now):
                    print(f"   {msg}")

                status = strategy.check_and_place(s, now)
                if status == "Outside entry window":
                    status = f"Waiting for {s['trigger_hour']:02d}:{s['trigger_minute']:02d} UTC"
                print(f"   {status}")
            except Exception as e:  # never let one bad cycle kill the bot
                print(f"   Cycle error: {e!r}")

        print("\nCycle done")
        sleep_until_next_poll(_utc_now())


if __name__ == "__main__":
    main()