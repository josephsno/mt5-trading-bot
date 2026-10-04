import time
from decouple import config, AutoConfig
from mt5.meter_trader_config import MetaTraderConfig
from strategies.scalp_strategy import (
    Scalp0600Strategy,
    _utc_now,
    ENTRY_HOUR,
    CLOSE_HOUR,
)
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
# Tight 1-second polling from 1 minute before the 06:00 entry window through
# 1 minute after the 07:00 close (entry window is only 2 minutes wide, and
# OCO / TP-SL re-anchoring should react fast once a side fills). Normal
# 30-second polling the rest of the day — nothing trades outside 06-07 UTC,
# the loop only cleans up leftovers then.
TIGHT_POLL_SECONDS = 1
NORMAL_POLL_SECONDS = 30
TIGHT_START_MINUTE = ENTRY_HOUR * 60 - 1  # 05:59 UTC
TIGHT_END_MINUTE = CLOSE_HOUR * 60 + 1  # 07:01 UTC


def sleep_until_next_tick(now: datetime, interval: int):
    """Sleeps until the next interval-aligned second mark (:00/:01/:02...
    for interval=1, :00/:30 for interval=30), not just 'interval seconds
    from whenever this was called' — keeps cycles clock-aligned rather
    than drifting."""
    seconds_to_wait = interval - (now.second % interval)
    if seconds_to_wait <= 0:
        seconds_to_wait = interval
    time.sleep(seconds_to_wait)


reload_decouple()

LIVE = False # Demo first — see scalp_0600_strategy.py's module docstring.

# Standalone process, own magic number (20261003), no shared loop with
# straddle_strategy.py or news_spike_strategy.py. Only ever touches its own
# XAUUSDm orders/positions.


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
    strategy = Scalp0600Strategy()

    print(f"{strategy}\n")
    print(f"Kill switch: {strategy.kill_switch()[1]}\n")

    # ── Main loop ─────────────────────────────────────────────────────────
    while True:
        # ONE timestamp per cycle, in broker time (VPS clock corrected
        # against the XAUUSDm tick — the VPS was once seen 64s off).
        now = _utc_now()
        minute = now.hour * 60 + now.minute
        tight = TIGHT_START_MINUTE <= minute <= TIGHT_END_MINUTE

        print("=" * 55)
        print(f"{now.strftime('%A %d %B %Y — %H:%M:%S UTC')}")
        print("=" * 55)
        print("\nXAUUSDm")

        try:
            # OCO cancel, TP/SL re-anchor to the real fill, and the 07:00
            # close/cancel of anything still open or pending.
            for msg in strategy.manage(now):
                print(f"   {msg}")

            status = strategy.check_and_place(now)
            if status == "Outside entry window":
                status = (
                    f"Not inside the {ENTRY_HOUR:02d}:00 UTC entry window"
                    f" — {strategy.kill_switch(now)[1]}"
                )
            print(f"   {status}")
        except Exception as e:  # never let one bad cycle kill the bot
            print(f"   Cycle error: {e!r}")

        print("\nCycle done")

        sleep_until_next_tick(
            _utc_now(), TIGHT_POLL_SECONDS if tight else NORMAL_POLL_SECONDS
        )


if __name__ == "__main__":
    main()