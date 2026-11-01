import time
from decouple import config, AutoConfig
from mt5.meter_trader_config import MetaTraderConfig
from strategies.index_dip_strategy import (
    IndexDipStrategy, _utc_now, last_check_at_or_before,
    CHECK_HOUR, CHECK_MINUTE, ENTRY_DELAY_MINUTES, CHECK_WINDOW_MINUTES,
)
from datetime import timedelta
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
# 60-second clock-aligned polling. The strategy only acts around 19:00 UTC:
#   18:50-18:59  history warm-up (no trading, nothing printed)
#   19:00        exits (retried every poll until the market is open)
#   19:01-19:10  entry decisions; ONE summary block per day, after that only
#                trades, exits, errors and "will retry" lines are printed
# Outside that: a heartbeat line every 15 minutes.
POLL_SECONDS = 60
HEARTBEAT_MINUTES = 15


def sleep_until_next_poll(now: datetime, interval: int = POLL_SECONDS):
    """Sleep until the next interval-aligned mark (e.g. 19:01:00), not just
    'interval seconds from now' — keeps cycles on the clock, no drift."""
    seconds_into = (now.minute * 60 + now.second) % interval
    seconds_to_wait = interval - seconds_into - now.microsecond / 1_000_000
    if seconds_to_wait <= 0:
        seconds_to_wait += interval
    time.sleep(seconds_to_wait)


reload_decouple()

LIVE = False  # new strategy — demo first. See index_dip_strategy.py.

# Standalone process. US30 20261028, USTEC 20261029, JP225 20261030,
# DE30 20261031 (UK100 20261032, disabled). Only ever touches its own positions.

QUIET = {"Outside check window"}
# repeated every minute of the window — shown once in the daily summary only
ROUTINE = ("Position still open", "Already traded at this check", "BREAKER:")


def is_routine(msg: str) -> bool:
    return msg.startswith(ROUTINE) or "-> no signal" in msg


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
    print(f"{strategy}\n")

    # ── Main loop ─────────────────────────────────────────────────────────
    last_minute = None       # printing/pacing only — no trading state
    summary_done_for = None  # date whose daily summary has been printed
    while True:
        # ONE timestamp per cycle, in broker time (VPS clock corrected
        # only for a real error >= 30 s — the VPS was once seen 64s off).
        now = _utc_now()
        minute = now.replace(second=0, microsecond=0)
        if minute == last_minute:          # never run the same minute twice
            sleep_until_next_poll(_utc_now())
            continue
        last_minute = minute

        check_at = last_check_at_or_before(now)
        in_window = (check_at + timedelta(minutes=ENTRY_DELAY_MINUTES) <= now
                     < check_at + timedelta(minutes=CHECK_WINDOW_MINUTES))
        show_summary = in_window and summary_done_for != check_at.date()

        lines = []
        retrying = False
        for s in strategy.setups:
            try:
                strategy.prime(s, now)     # 18:50-18:59 only: sync history
                # Exit FIRST, every cycle, unconditionally: closes yesterday's
                # position before today's signal is checked.
                msgs = list(strategy.manage(s, now))
                status = strategy.check_and_place(s, now)
                if status not in QUIET:
                    msgs.append(status)
                if any(m.endswith("will retry") for m in msgs):
                    retrying = True
                if not show_summary:
                    msgs = [m for m in msgs if not is_routine(m)]
                if msgs:
                    lines.append(f"\n{s['name']} ({s['symbol']})")
                    lines.extend(f"   {m}" for m in msgs)
            except Exception as e:  # never let one bad cycle kill the bot
                lines.append(f"\n{s['name']} ({s['symbol']})")
                lines.append(f"   Cycle error: {e!r}")
        if show_summary and not retrying:   # summary stands once every index decided
            summary_done_for = check_at.date()

        if lines:
            print("=" * 55)
            print(f"{now.strftime('%A %d %B %Y — %H:%M:%S UTC')}")
            print("=" * 55)
            for line in lines:
                print(line)
            print("\nCycle done")
        elif now.minute % HEARTBEAT_MINUTES == 0:
            print(f"{now:%a %d %b %H:%M} UTC — alive, next check "
                  f"{CHECK_HOUR:02d}:{CHECK_MINUTE:02d} UTC (Mon-Fri)")

        sleep_until_next_poll(_utc_now())


if __name__ == "__main__":
    main()