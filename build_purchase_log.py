"""
build_purchase_log.py — Kraken BTC purchase log for Skatteetaten cost-basis tracking.

Fetches BTC/EUR trade history from Kraken, enriches each trade with the
historical NOK/EUR exchange rate, and appends new rows to purchase_log.csv.

Intended to run as a daily systemd timer, shortly after buy_bitcoin.py.

Requirements:
    - KRAKEN_QUERY_API_KEY and KRAKEN_QUERY_API_SECRET in .env
      (read-only key: only "Query Closed Orders & Trades" permission needed)

CSV columns:
    timestamp_utc    — trade execution time (UTC)
    txid             — Kraken trade ID
    btc_amount       — BTC received (after fees)
    eur_amount       — EUR spent (excluding fee)
    fee_eur          — Kraken fee in EUR
    total_cost_eur   — EUR spent including fee (eur_amount + fee_eur)
    btc_price_eur    — effective price per BTC in EUR
    nok_per_eur      — historical NOK/EUR rate on the trade date
    btc_price_nok    — BTC price in NOK
    cost_basis_nok   — total acquisition cost in NOK (total_cost_eur * nok_per_eur)
"""

import csv
import json
import logging
import os
import time
from datetime import datetime, timezone

import krakenex
import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

API_KEY = os.getenv("KRAKEN_QUERY_API_KEY")
API_SECRET = os.getenv("KRAKEN_QUERY_API_SECRET")

PURCHASE_LOG = os.path.join(BASE_DIR, "purchase_log.csv")
STATE_FILE = os.path.join(BASE_DIR, "purchase_log_state.json")

# Only log BTC/EUR trades (Kraken internal pair name)
BTC_EUR_PAIR = "XXBTZEUR"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log_dir = os.path.join(BASE_DIR, "logs")
os.makedirs(log_dir, exist_ok=True)

logger = logging.getLogger("PurchaseLog")
logger.setLevel(logging.INFO)
handler = logging.handlers.RotatingFileHandler(
    os.path.join(log_dir, "purchase_log.txt"),
    maxBytes=1_000_000,
    backupCount=3,
)
handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(handler)

# Also log to stdout so systemd journal captures it
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(stream_handler)

# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

CSV_HEADERS = [
    "timestamp_utc",
    "txid",
    "btc_amount",
    "eur_amount",
    "fee_eur",
    "total_cost_eur",
    "btc_price_eur",
    "nok_per_eur",
    "btc_price_nok",
    "cost_basis_nok",
]


def load_existing_txids():
    """Return the set of txids already recorded in the CSV."""
    if not os.path.exists(PURCHASE_LOG):
        return set()
    with open(PURCHASE_LOG, newline="") as f:
        reader = csv.DictReader(f)
        return {row["txid"] for row in reader}


def append_rows(rows):
    """Append a list of dicts to the CSV, writing headers if the file is new."""
    file_exists = os.path.exists(PURCHASE_LOG)
    with open(PURCHASE_LOG, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)
    logger.info("Appended %d new row(s) to %s", len(rows), PURCHASE_LOG)


# ---------------------------------------------------------------------------
# State (last-processed timestamp)
# ---------------------------------------------------------------------------

def load_state():
    """Load state. Returns {'last_trade_time': float or None}."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Could not read state file, starting fresh: %s", e)
    return {"last_trade_time": None}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    logger.info("State saved: last_trade_time=%s", state.get("last_trade_time"))


# ---------------------------------------------------------------------------
# Kraken
# ---------------------------------------------------------------------------

def fetch_trades(kraken, start=None):
    """Fetch all BTC/EUR buy trades from Kraken, optionally from a start timestamp.

    Handles Kraken's pagination (ofs parameter) automatically.

    Returns:
        List of trade dicts, sorted by time ascending.
    """
    all_trades = {}
    offset = 0

    while True:
        params = {"type": "buy", "ofs": offset}
        if start is not None:
            params["start"] = start

        response = kraken.query_private("TradesHistory", params)
        if response.get("error"):
            logger.error("Kraken TradesHistory error: %s", response["error"])
            return None

        result = response.get("result", {})
        trades = result.get("trades", {})
        count = result.get("count", 0)

        if not trades:
            break

        # Filter to BTC/EUR buys only
        btc_trades = {
            txid: t for txid, t in trades.items()
            if t.get("pair") == BTC_EUR_PAIR and t.get("type") == "buy"
        }
        all_trades.update(btc_trades)
        logger.info(
            "Fetched batch: %d trades (offset %d / count %d), %d BTC/EUR buys",
            len(trades), offset, count, len(btc_trades),
        )

        offset += len(trades)
        if offset >= count:
            break

        # Be polite to the API between pages
        time.sleep(1)

    # Sort by trade time ascending
    sorted_trades = sorted(all_trades.items(), key=lambda x: float(x[1]["time"]))
    logger.info("Total BTC/EUR buy trades fetched: %d", len(sorted_trades))
    return sorted_trades


# ---------------------------------------------------------------------------
# Exchange rates (Frankfurter API — historical daily rates)
# ---------------------------------------------------------------------------

_rate_cache = {}


def get_nok_per_eur(date_str):
    """Return the NOK/EUR rate for the given date (YYYY-MM-DD).

    Uses Frankfurter API for historical rates. Results are cached in memory
    to avoid duplicate requests when multiple trades fall on the same day.
    Falls back to latest rate if historical lookup fails.
    """
    if date_str in _rate_cache:
        return _rate_cache[date_str]

    try:
        url = f"https://api.frankfurter.app/{date_str}?from=EUR&to=NOK"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if "rates" in data and "NOK" in data["rates"]:
            rate = data["rates"]["NOK"]
            _rate_cache[date_str] = rate
            logger.info("NOK/EUR rate on %s: %.4f", date_str, rate)
            return rate
        logger.warning("Unexpected Frankfurter response for %s: %s", date_str, data)
    except Exception as e:
        logger.error("Error fetching NOK/EUR rate for %s: %s", date_str, e)

    # Fallback: try latest rate
    logger.warning("Falling back to latest NOK/EUR rate for %s", date_str)
    try:
        resp = requests.get("https://api.frankfurter.app/latest?from=EUR&to=NOK", timeout=10)
        data = resp.json()
        if "rates" in data and "NOK" in data["rates"]:
            rate = data["rates"]["NOK"]
            logger.warning("Using latest NOK/EUR rate (%.4f) as fallback for %s", rate, date_str)
            _rate_cache[date_str] = rate
            return rate
    except Exception as e:
        logger.error("Fallback rate fetch also failed: %s", e)

    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not API_KEY or not API_SECRET:
        logger.error(
            "KRAKEN_QUERY_API_KEY and KRAKEN_QUERY_API_SECRET must be set in .env"
        )
        return

    kraken = krakenex.API(API_KEY, API_SECRET)

    state = load_state()
    existing_txids = load_existing_txids()
    start_ts = state.get("last_trade_time")

    if start_ts is None:
        logger.info("No prior state — performing full history pull.")
    else:
        logger.info(
            "Fetching trades since %s",
            datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat(),
        )

    trades = fetch_trades(kraken, start=start_ts)
    if trades is None:
        logger.error("Failed to fetch trades. Exiting.")
        return

    if not trades:
        logger.info("No new trades found.")
        return

    new_rows = []
    latest_ts = start_ts

    for txid, trade in trades:
        if txid in existing_txids:
            logger.info("Skipping already-logged trade: %s", txid)
            continue

        trade_ts = float(trade["time"])
        trade_dt = datetime.fromtimestamp(trade_ts, tz=timezone.utc)
        date_str = trade_dt.strftime("%Y-%m-%d")
        timestamp_utc = trade_dt.strftime("%Y-%m-%d %H:%M:%S")

        btc_amount = float(trade["vol"])       # BTC received
        eur_amount = float(trade["cost"])      # EUR spent (excl. fee)
        fee_eur = float(trade["fee"])          # Kraken fee in EUR
        total_cost_eur = eur_amount + fee_eur
        btc_price_eur = float(trade["price"])  # actual executed price

        nok_per_eur = get_nok_per_eur(date_str)
        if nok_per_eur is None:
            logger.error(
                "Could not get NOK/EUR rate for %s — skipping trade %s. "
                "Re-run once rates are available.",
                date_str, txid,
            )
            continue

        btc_price_nok = btc_price_eur * nok_per_eur
        cost_basis_nok = total_cost_eur * nok_per_eur

        new_rows.append({
            "timestamp_utc": timestamp_utc,
            "txid": txid,
            "btc_amount": f"{btc_amount:.8f}",
            "eur_amount": f"{eur_amount:.2f}",
            "fee_eur": f"{fee_eur:.4f}",
            "total_cost_eur": f"{total_cost_eur:.2f}",
            "btc_price_eur": f"{btc_price_eur:.2f}",
            "nok_per_eur": f"{nok_per_eur:.4f}",
            "btc_price_nok": f"{btc_price_nok:.2f}",
            "cost_basis_nok": f"{cost_basis_nok:.2f}",
        })

        if latest_ts is None or trade_ts > latest_ts:
            latest_ts = trade_ts

    if new_rows:
        append_rows(new_rows)
        state["last_trade_time"] = latest_ts
        save_state(state)
        logger.info("Done. %d new trade(s) logged.", len(new_rows))
    else:
        logger.info("No new trades to log.")


if __name__ == "__main__":
    import logging.handlers  # ensure available at module level for logger setup
    main()
