# main_news_spike.py
import time
from decouple import config, AutoConfig
from mt5.meter_trader_config import MetaTraderConfig
from strategies.news_spike_strategy import (
    NewsSpikeStrategy,
    _utc_now,
    next_release,
    RELEASES,
    HOLD_SECONDS,
    GLOBAL_FLATTEN_RETRY_SECONDS,
)
from datetime import datetime, timedelta
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
# The entry window is only EARLY_ENTRY_SECONDS (2 s) wide, so polling is
# 1 s, aligned to whole seconds (:58.0, :59.0 ...), from TIGHT_BAND_MINUTES
# before each release until release + 60 s + 15 s. Otherwise every 30 s.
TIGHT_POLL_SECONDS = 1
NORMAL_POLL_SECONDS = 30
TIGHT_BAND_MINUTES = 2


def in_tight_band(now: datetime) -> bool:
    """1 s polling from 2 min before a release until release + 60 s + 15 s."""
    for rel, _ in RELEASES:
        if rel - timedelta(minutes=TIGHT_BAND_MINUTES) <= now <= \
                rel + timedelta(seconds=HOLD_SECONDS + GLOBAL_FLATTEN_RETRY_SECONDS):
            return True
    return False


def sleep_until_next_mark(interval: int):
    """Sleep to the next interval-aligned mark in BROKER time, including the
    fraction of a second (the old version ignored microseconds and drifted)."""
    now = _utc_now()
    into = (now.minute * 60 + now.second) % interval + now.microsecond / 1_000_000
    wait = interval - into
    if wait <= 0.01:
        wait += interval
    time.sleep(wait)


reload_decouple()

LIVE = False  # news straddle back at 1% risk — demo first on the next release.

# Standalone process, own magic (20260807). Touches only its own trades unless
# FLATTEN_OTHER_BOTS is switched on in news_spike_strategy.py.


def main():

    # ── MT5 ──────────────────────────────────────────────────────────────
    mt5_config = MetaTraderConfig()
    mt5_settings = {
        "username": config("MT5_USERNAME" if LIVE else "MT5_USERNAME_TRIAL"),
        "password": config("MT5_PASSWORD" if LIVE else "MT5_PASSWORD_TRIAL"),
        "server": config("MT5_SERVER" if LIVE else "MT5_SERVER_TRIAL"),
        "mt5_pathway": config("MT5_PATHWAY"),
    }

    # never print the settings dict — it holds the password
    print(f"Mode: {'LIVE' if LIVE else 'DEMO'} | "
          f"account {mt5_settings['username']} on {mt5_settings['server']}")

    if not mt5_config.start_mt5(mt5_settings):
        print("MT5 failed to start")
        return

    # ── Strategy ─────────────────────────────────────────────────────────
    strategy = NewsSpikeStrategy()
    print(f"{strategy}\n")
    warn = strategy.dates_warning(_utc_now())
    if warn:
        print(warn)

    # ── Main loop ─────────────────────────────────────────────────────────
    last_status = None
    last_alive = None
    while True:
        # ONE broker-corrected timestamp per cycle (the old loop used the raw
        # VPS clock here, so the clock correction never reached the entry check)
        now = _utc_now()
        tight = in_tight_band(now)
        lines = []
        try:
            lines.extend(strategy.manage(now))
            status = strategy.check_and_place(now)
            if status != "Outside entry window" and status != last_status:
                lines.append(status)
            last_status = status
        except Exception as e:  # never let one bad cycle kill the bot
            lines.append(f"Cycle error: {e!r}")

        hourly = last_alive is None or now - last_alive >= timedelta(hours=1)
        if lines or hourly:
            print(f"[{now:%a %d %b %Y %H:%M:%S}.{now.microsecond // 1000:03d} UTC]")
            for ln in lines:
                print(f"   {ln}")
            if not lines:
                nxt = next_release(now)
                print(f"   Alive — next release: {nxt[1]} {nxt[0]:%a %d %b %H:%M:%S} UTC" if nxt
                      else "   Alive — NO releases left in the calendar: add dates")
                warn = strategy.dates_warning(now)
                if warn:
                    print(f"   {warn}")
            if hourly:
                last_alive = now

        sleep_until_next_mark(TIGHT_POLL_SECONDS if tight else NORMAL_POLL_SECONDS)


if __name__ == "__main__":
    main()