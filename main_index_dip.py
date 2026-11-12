# main_index_dip.py
import time
from decouple import config, AutoConfig
from mt5.meter_trader_config import MetaTraderConfig
from strategies.index_dip_strategy import IndexDipStrategy, _utc_now
from strategies.pre_fomc_strategy import PreFomcStrategy
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
# 1-minute clock-aligned polling (:00, :01, :02 ...). Needed because:
#   18:50-18:59  prime(): fetch H1/D1 every poll so MT5 syncs history first
#   19:00        manage(): yesterday's position is closed
#   19:01-19:30  check_and_place(): decision (never at 19:00:00), retried each
#                minute while history is not trusted yet
# Pre-FOMC (same process, own magics): buy 14:00 NY the day before each Fed
# decision, close 13:59 NY decision day — needs the 1-minute polls too.
POLL_SECONDS = 60


def sleep_until_next_poll(now: datetime, interval: int = POLL_SECONDS):
    """Sleep until the next interval-aligned mark (e.g. 19:01:00), not just
    'interval seconds from now' — keeps cycles on the clock, no drift."""
    seconds_into = (now.minute * 60 + now.second) % interval
    seconds_to_wait = interval - seconds_into - now.microsecond / 1_000_000
    if seconds_to_wait <= 0:
        seconds_to_wait += interval
    time.sleep(seconds_to_wait)


reload_decouple()

LIVE = True  # dip bot is live (USTEC 20261029, US30 20261028). False = demo account.

# Standalone process: index dip (magics 20261028/29) + pre-FOMC (20261034/35/36).
# Each part only ever touches its own orders/positions.


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
    strategy = IndexDipStrategy()
    fomc = PreFomcStrategy()
    print(f"{strategy}\n{fomc}\n")

    # ── Main loop ─────────────────────────────────────────────────────────
    last_status = {}
    while True:
        now = _utc_now()
        lines = []
        for s in strategy.setups:
            try:
                strategy.prime(s, now)                       # 18:50-18:59 only
                # Management FIRST, every cycle, unconditionally: exits due,
                # stop self-heal. Uses broker time internally.
                for msg in strategy.manage(s, now):
                    lines.append(f"{s['name']}: {msg}")
                status = strategy.check_and_place(s, now)
                # print the decision block only when it changes (no spam every minute)
                if status != "Outside check window" and status != last_status.get(s["name"]):
                    lines.append(status)
                last_status[s["name"]] = status
            except Exception as e:  # never let one bad cycle kill the bot
                lines.append(f"{s['name']}: Cycle error: {e!r}")

        for s in fomc.setups:
            try:
                lines.extend(fomc.manage(s, now))
                status = fomc.check_and_place(s, now)
                if status != "Outside entry window" and status != last_status.get(s["name"]):
                    lines.append(status)
                last_status[s["name"]] = status
            except Exception as e:  # never let one bad cycle kill the bot
                lines.append(f"{s['name']}: Cycle error: {e!r}")
        warn = fomc.dates_warning(now)
        if warn and now.minute == 0 and now.hour == 12:
            lines.append(warn)

        if lines or (now.minute == 0 and now.hour % 4 == 0):
            print("=" * 55)
            print(f"{now.strftime('%A %d %B %Y — %H:%M:%S UTC')}")
            print("=" * 55)
            for ln in lines:
                print(ln)
            if not lines:
                print("Alive — dip check 19:01 UTC (Mon-Fri)" + (f"; next Fed decision {fomc.next_meeting(now)}" if fomc.next_meeting(now) else ""))
            print()
        sleep_until_next_poll(_utc_now())


if __name__ == "__main__":
    main()