"""portfolio_trend.py — BTC holdings vs. a NOK savings account, over time.

For every purchase in purchase_log.csv, compares:
  - invested_nok       cumulative NOK actually spent
  - btc_value_nok      what your BTC holdings were worth in NOK, at that point in time
  - savings_value_nok  what those same NOK amounts would be worth today if each
                        had instead gone into a savings account at ANNUAL_RATE,
                        compounding from its purchase date

Run this on the machine where purchase_log.csv lives (btc-script), then copy
the JSON output (or the trend_data.json file it writes) elsewhere for charting.
"""

import argparse
import csv
import json
import logging
import os
from datetime import datetime, timezone

from common import fetch_exchange_rates, kraken

_log = logging.getLogger(__name__)
_log.addHandler(logging.StreamHandler())
_log.setLevel(logging.WARNING)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PURCHASE_LOG = os.path.join(BASE_DIR, "purchase_log.csv")
OUT_FILE = os.path.join(BASE_DIR, "trend_data.json")

DEFAULT_ANNUAL_RATE = 0.04  # 4% — typical NOK high-yield savings account
DEFAULT_SAMPLE_DAYS = 7  # weekly points, to keep the series small


def load_purchases():
    if not os.path.exists(PURCHASE_LOG):
        raise SystemExit("purchase_log.csv not found — run build_purchase_log.py first.")
    with open(PURCHASE_LOG, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["_dt"] = datetime.strptime(
            r["timestamp_utc"], "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)
        r["_btc"] = float(r["btc_amount"])
        r["_cost_nok"] = float(r["cost_basis_nok"])
        r["_price_nok"] = float(r["btc_price_nok"])
    rows.sort(key=lambda r: r["_dt"])
    return rows


def savings_value_at(rows, as_of, annual_rate):
    """Value today (as_of) of every NOK amount spent on or before as_of,
    had each instead been deposited into a savings account on its spend date."""
    total = 0.0
    for r in rows:
        if r["_dt"] > as_of:
            break
        days_elapsed = (as_of - r["_dt"]).days
        total += r["_cost_nok"] * (1 + annual_rate) ** (days_elapsed / 365)
    return total


def build_trend(rows, annual_rate, sample_every_days):
    trend = []
    cum_btc = 0.0
    cum_invested = 0.0
    last_sample_date = None

    for i, r in enumerate(rows):
        cum_btc += r["_btc"]
        cum_invested += r["_cost_nok"]

        d = r["_dt"].date()
        is_last = i == len(rows) - 1
        if not is_last and last_sample_date is not None and (d - last_sample_date).days < sample_every_days:
            continue
        last_sample_date = d

        trend.append(
            {
                "date": d.isoformat(),
                "invested_nok": round(cum_invested, 2),
                "btc_value_nok": round(cum_btc * r["_price_nok"], 2),
                "savings_value_nok": round(savings_value_at(rows, r["_dt"], annual_rate), 2),
            }
        )

    return trend, cum_btc, cum_invested


def append_live_point(trend, cum_btc, cum_invested, rows, annual_rate):
    """Add a final point using the live BTC price, so the trend reflects right now."""
    nok_per_eur, _ = fetch_exchange_rates(_log)
    ticker = kraken.query_public("Ticker", {"pair": "XXBTZEUR"})
    if ticker.get("error") or nok_per_eur is None:
        return  # fall back silently to last purchase-day point
    btc_price_eur = float(ticker["result"]["XXBTZEUR"]["a"][0])
    btc_price_nok = btc_price_eur * nok_per_eur

    now = datetime.now(timezone.utc)
    trend.append(
        {
            "date": now.date().isoformat(),
            "invested_nok": round(cum_invested, 2),
            "btc_value_nok": round(cum_btc * btc_price_nok, 2),
            "savings_value_nok": round(savings_value_at(rows, now, annual_rate), 2),
        }
    )


def main():
    parser = argparse.ArgumentParser(description="BTC vs. NOK savings account, over time")
    parser.add_argument("--rate", type=float, default=DEFAULT_ANNUAL_RATE, help="Annual savings interest rate, e.g. 0.04 for 4%%")
    parser.add_argument("--sample-days", type=int, default=DEFAULT_SAMPLE_DAYS, help="Days between sampled points")
    parser.add_argument("--no-live", action="store_true", help="Skip the live-price final point")
    args = parser.parse_args()

    rows = load_purchases()
    trend, cum_btc, cum_invested = build_trend(rows, args.rate, args.sample_days)

    if not args.no_live:
        append_live_point(trend, cum_btc, cum_invested, rows, args.rate)

    with open(OUT_FILE, "w") as f:
        json.dump({"annual_rate": args.rate, "points": trend}, f, indent=2)

    print(json.dumps({"annual_rate": args.rate, "points": trend}, indent=2))
    print(f"\nWrote {len(trend)} points to {OUT_FILE}", flush=True)


if __name__ == "__main__":
    main()
