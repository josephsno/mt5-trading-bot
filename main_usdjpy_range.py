# main_usdjpy_asian_range.py
import time
from decouple import config, AutoConfig
from mt5.meter_trader_config import MetaTraderConfig
from strategies.usdjpy_asian_range_strategy import UsdJpyAsianRangeStrategy, _utc_now
from datetime import timedelta
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
# 1-minute clock-aligned polling: orders 00:00 NY (window to 00:10), exit 09:00 NY;
# every other poll only cleans up anything left outside 00:00-09:00 NY.
POLL_SECONDS = 60


def sleep_until_next_poll(interval: int = POLL_SECONDS):
    now = _utc_now()
    into = (now.minute * 60 + now.second) % interval + now.microsecond / 1_000_000
    wait = interval - into
    if wait <= 0.01:
        wait += interval
    time.sleep(wait)


reload_decouple()

LIVE = False  # new strategy — demo first (register: strategy 6).

# Standalone process, magic 20261038. Only ever touches its own positions.


def main():

    # ── MT5 ──────────────────────────────────────────────────────────────
    mt5_config = MetaTraderConfig()
    mt5_settings = {
        "username": config("MT5_USERNAME" if LIVE else "MT5_USERNAME_TRIAL"),
        "password": config("MT5_PASSWORD" if LIVE else "MT5_PASSWORD_TRIAL"),
        "server": config("MT5_SERVER" if LIVE else "MT5_SERVER_TRIAL"),
        "mt5_pathway": config("MT5_PATHWAY"),
    }

    print(f"Mode: {'LIVE' if LIVE else 'DEMO'} | "
          f"account {mt5_settings['username']} on {mt5_settings['server']}")

    if not mt5_config.start_mt5(mt5_settings):
        print("MT5 failed to start")
        return

    # ── Strategy ─────────────────────────────────────────────────────────
    strategy = UsdJpyAsianRangeStrategy()
    print(f"{strategy}\n")

    # ── Main loop ─────────────────────────────────────────────────────────
    last_status = None
    last_alive = None
    while True:
        now = _utc_now()   # broker-corrected, one timestamp per cycle
        lines = []
        try:
            lines.extend(strategy.manage(now))
            status = strategy.check_and_place(now)
            if status != "Outside entry window" and status != last_status:
                lines.append(status)
            last_status = status
        except Exception as e:  # never let one bad cycle kill the bot
            lines.append(f"Cycle error: {e!r}")

        hourly = last_alive is None or now - last_alive >= timedelta(hours=4)
        if lines or hourly:
            print("=" * 55)
            print(f"{now.strftime('%A %d %B %Y — %H:%M:%S UTC')}")
            print("=" * 55)
            for ln in lines:
                print(ln)
            if not lines:
                print("Alive — orders go in at 00:00 New York (Mon-Fri)")
            if hourly:
                last_alive = now
            print()
        sleep_until_next_poll()


if __name__ == "__main__":
    main()