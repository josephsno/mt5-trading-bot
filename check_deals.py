"""
Print every deal on the account since yesterday for the index symbols:
who opened/closed it (magic + comment) and why. Read-only — places no orders.

Run on the VPS:   python check_deals_today.py
Uses the same .env keys as the bots (LIVE account).
"""
import datetime

import MetaTrader5 as mt5
from decouple import config

SYMBOLS = {"US30m", "USTECm", "JP225m", "DE30m"}
ENTRY = {0: "IN", 1: "OUT", 2: "INOUT", 3: "OUT_BY"}
REASON = {0: "manual (desktop)", 1: "mobile app", 2: "web", 3: "bot/EA",
          4: "STOP-LOSS", 5: "TAKE-PROFIT", 6: "STOP-OUT (margin)",
          7: "rollover", 8: "variation margin", 9: "split"}

ok = mt5.initialize(path=config("MT5_PATHWAY"), login=int(config("MT5_USERNAME")),
                    password=config("MT5_PASSWORD"), server=config("MT5_SERVER"))
if not ok:
    raise SystemExit(f"initialize() failed: {mt5.last_error()}")

acc = mt5.account_info()
print(f"Account {acc.login} on {acc.server}, balance {acc.balance:.2f}\n")

now = datetime.datetime.now(datetime.timezone.utc)
start = now - datetime.timedelta(days=2)
# No group= filter: it returns nothing on this account (2026-09-15 fix).
deals = [d for d in (mt5.history_deals_get(start, now + datetime.timedelta(hours=1)) or ())
         if d.symbol in SYMBOLS]
if not deals:
    print("No deals on these symbols in the last 2 days.")
for d in sorted(deals, key=lambda d: d.time):
    t = datetime.datetime.fromtimestamp(d.time, tz=datetime.timezone.utc)
    print(f"{t:%a %d %b %H:%M:%S} UTC  {d.symbol:7s} {ENTRY.get(d.entry, d.entry):6s} "
          f"{'BUY ' if d.type == 0 else 'SELL'} {d.volume} @ {d.price}  "
          f"P&L {d.profit:+.2f}  magic {d.magic}  comment '{d.comment}'  "
          f"by: {REASON.get(d.reason, d.reason)}  position {d.position_id}")

print("\nOpen positions on these symbols now:")
pos = [p for p in (mt5.positions_get() or ()) if p.symbol in SYMBOLS]
for p in pos:
    print(f"  {p.symbol} ticket {p.ticket} {p.volume} @ {p.price_open} SL {p.sl} magic {p.magic}")
if not pos:
    print("  none")
mt5.shutdown()