import krakenex
import logging
from logging.handlers import RotatingFileHandler
import requests
import os
import json
from datetime import date
from dotenv import load_dotenv

load_dotenv()

# Kraken info
API_KEY = os.getenv("KRAKEN_API_KEY")
API_SECRET = os.getenv("KRAKEN_API_SECRET")

# Initialize Kraken API
kraken = krakenex.API(API_KEY, API_SECRET)

# Telegram info
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


# Setup logging
log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "btc_log.txt")

logger = logging.getLogger("buy_bitcoin")
logger.setLevel(logging.INFO)

# Configure a rotating handler - up to 5 files of 1MB each
handler = RotatingFileHandler(
    log_file,
    maxBytes=1_000_000,  # 1 MB
    backupCount=5,
)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

# DCA config
DAILY_BUY_EUR = 15       # EUR added to the pot each day
FORCE_BUY_DAYS = 7       # Force a buy after this many days regardless of price
MA_PERIOD = 14           # Number of days for the moving average
PAIR = "XXBTZEUR"

# State file — tracks accumulated budget and days since last buy
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state() -> dict:
    """Load DCA state from disk. Returns defaults if file doesn't exist."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {
        "accumulated_eur": DAILY_BUY_EUR,
        "days_since_last_buy": 1,
        "last_run_date": str(date.today()),
    }


def save_state(state: dict) -> None:
    """Persist DCA state to disk."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Price helpers
# ---------------------------------------------------------------------------

def get_btc_price() -> float | None:
    """Fetch the current BTC ask price in EUR."""
    ticker = kraken.query_public("Ticker", {"pair": PAIR})
    if "error" in ticker and ticker["error"]:
        logger.error("Error fetching BTC price: %s", ticker["error"])
        return None
    price = float(ticker["result"][PAIR]["a"][0])
    logger.info("Current BTC price: %.2f EUR/BTC", price)
    return price


def get_14day_ma() -> float | None:
    """Fetch daily OHLC data and compute the MA_PERIOD-day simple moving average of close prices."""
    ohlc = kraken.query_public("OHLC", {"pair": PAIR, "interval": 1440})
    if "error" in ohlc and ohlc["error"]:
        logger.error("Error fetching OHLC data: %s", ohlc["error"])
        return None

    # OHLC entries: [time, open, high, low, close, vwap, volume, count]
    candles = ohlc["result"].get(PAIR, [])
    if len(candles) < MA_PERIOD:
        logger.error("Not enough OHLC data for MA calculation (got %d candles)", len(candles))
        return None

    # Use the last MA_PERIOD closed candles (exclude the current in-progress candle)
    closes = [float(c[4]) for c in candles[-(MA_PERIOD + 1):-1]]
    ma = sum(closes) / len(closes)
    logger.info("%d-day MA: %.2f EUR/BTC", MA_PERIOD, ma)
    return ma


# ---------------------------------------------------------------------------
# Buy execution
# ---------------------------------------------------------------------------

def execute_buy(amount_eur: float, btc_price: float) -> bool:
    """Place a market buy order on Kraken. Returns True on success."""
    btc_amount = round(amount_eur / btc_price, 8)
    logger.info(
        "Attempting to buy %.8f BTC at %.2f EUR/BTC for %.2f EUR",
        btc_amount, btc_price, amount_eur,
    )

    order = kraken.query_private(
        "AddOrder",
        {
            "pair": PAIR,
            "type": "buy",
            "ordertype": "market",
            "volume": str(btc_amount),
        },
    )

    if "error" in order and order["error"]:
        logger.error("Kraken API Error: %s", order["error"])
        send_telegram(f"❌ BTC Purchase FAILED\nError: {order['error']}")
        return False

    logger.info("BTC Purchase Successful: %s", order)
    return True


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(message: str) -> None:
    """Send a message via the configured Telegram bot."""
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        logger.error("Failed to send Telegram message: %s", e)


# ---------------------------------------------------------------------------
# Exchange rates / account status (unchanged from original)
# ---------------------------------------------------------------------------

def fetch_exchange_rates():
    """
    Fetch the NOK/EUR exchange rate and calculate the NOK/BTC rate.
    Returns:
        tuple: (nok_per_eur, btc_price_nok) if successful, otherwise (None, None)
    """
    try:
        response = requests.get("https://api.frankfurter.app/latest?from=NOK&to=EUR", timeout=10)
        data = response.json()
        if "rates" not in data or "EUR" not in data["rates"]:
            logger.error("Missing expected data in exchange rate response: %s", data)
            return None, None

        eur_rate = data["rates"]["EUR"]
        nok_per_eur = 1 / eur_rate

        ticker = kraken.query_public("Ticker", {"pair": PAIR})
        if "error" in ticker and ticker["error"]:
            logger.error("Error fetching BTC price: %s", ticker["error"])
            return nok_per_eur, None

        btc_price_eur = float(ticker["result"][PAIR]["a"][0])
        btc_price_nok = btc_price_eur * nok_per_eur

        return nok_per_eur, btc_price_nok

    except requests.exceptions.RequestException as e:
        logger.error("Network error fetching exchange rates: %s", e)
        return None, None
    except Exception as e:
        logger.error("Unexpected error fetching exchange rates: %s", e)
        return None, None


def fetch_kraken_balance():
    """
    Fetch the Kraken account balance in EUR and BTC.
    Returns:
        tuple: (eur_balance, btc_balance) if successful, otherwise (None, None)
    """
    balance = kraken.query_private("Balance")
    if "error" in balance and balance["error"]:
        logger.error("Error fetching Kraken balance: %s", balance["error"])
        return None, None

    eur_balance = float(balance["result"].get("ZEUR", 0))
    btc_balance = float(balance["result"].get("XXBT", 0))
    return eur_balance, btc_balance


def print_account_status():
    """
    Log and send the account status via Telegram.
    """
    nok_per_eur, btc_price_nok = fetch_exchange_rates()
    if nok_per_eur is None or btc_price_nok is None:
        logger.error("Could not fetch exchange rates. Skipping log update.")
        return

    eur_balance, btc_balance = fetch_kraken_balance()
    if eur_balance is None or btc_balance is None:
        logger.error("Could not fetch Kraken balance.")
        return

    eur_balance_nok = eur_balance * nok_per_eur
    btc_balance_nok = btc_balance * btc_price_nok
    daily_buy_nok = DAILY_BUY_EUR * nok_per_eur
    days_left = eur_balance / DAILY_BUY_EUR if eur_balance > 0 else 0

    logger.info("Kraken EUR Balance: %.2f EUR (%.2f NOK)", eur_balance, eur_balance_nok)
    logger.info("Kraken BTC Balance: %.8f BTC (%.2f NOK)", btc_balance, btc_balance_nok)
    logger.info("Daily BTC purchase: %.2f EUR (%.2f NOK)", DAILY_BUY_EUR, daily_buy_nok)
    logger.info("Kraken EUR Balance will be empty in: %.1f days.", days_left)

    message = (
        "Kraken EUR Balance: {:.2f} EUR ({:.2f} NOK)\n"
        "Kraken BTC Balance: {:.8f} BTC ({:.2f} NOK)\n"
        "Daily BTC purchase: {:.2f} EUR ({:.2f} NOK)\n"
        "Kraken EUR Balance will be empty in: {:.1f} days."
    ).format(
        eur_balance, eur_balance_nok,
        btc_balance, btc_balance_nok,
        DAILY_BUY_EUR, daily_buy_nok,
        days_left,
    )
    send_telegram(message)


# ---------------------------------------------------------------------------
# Main DCA logic
# ---------------------------------------------------------------------------

def run_dca():
    """
    Spring-loaded DCA algorithm:
    - Adds DAILY_BUY_EUR to the pot each day.
    - Buys if price < 14-day MA, or if FORCE_BUY_DAYS days have passed without a buy.
    - On a skip day, accumulates the budget for the next dip.
    """
    state = load_state()
    today = str(date.today())

    # Guard: only run once per calendar day
    if state.get("last_run_date") == today:
        logger.info("Already ran today (%s). Exiting.", today)
        return

    # Add today's daily allocation to the pot
    state["accumulated_eur"] += DAILY_BUY_EUR
    state["days_since_last_buy"] += 1
    state["last_run_date"] = today

    logger.info(
        "Day %d since last buy. Pot: %.2f EUR.",
        state["days_since_last_buy"], state["accumulated_eur"],
    )

    # Fetch price data
    btc_price = get_btc_price()
    if btc_price is None:
        send_telegram("⚠️ DCA skipped: could not fetch BTC price.")
        save_state(state)
        return

    ma = get_14day_ma()
    if ma is None:
        send_telegram("⚠️ DCA skipped: could not compute 14-day MA.")
        save_state(state)
        return

    # Decision logic
    price_below_ma = btc_price < ma
    forced = state["days_since_last_buy"] >= FORCE_BUY_DAYS

    if price_below_ma or forced:
        reason = "price below 14-day MA" if price_below_ma else f"forced after {state['days_since_last_buy']} days"
        logger.info("Buying %.2f EUR — %s", state["accumulated_eur"], reason)

        success = execute_buy(state["accumulated_eur"], btc_price)

        if success:
            buy_type = "🟢 Dip buy" if price_below_ma else "🟡 Forced buy"
            msg = (
                f"{buy_type} — {reason}\n"
                f"Bought: {state['accumulated_eur']:.2f} EUR worth of BTC\n"
                f"Price: {btc_price:,.2f} EUR | 14d MA: {ma:,.2f} EUR"
            )
            send_telegram(msg)

            # Reset state after successful buy
            state["accumulated_eur"] = 0.0
            state["days_since_last_buy"] = 0
    else:
        logger.info(
            "Skipping buy. Price %.2f EUR is above MA %.2f EUR. Pot now %.2f EUR.",
            btc_price, ma, state["accumulated_eur"],
        )
        msg = (
            f"⏸ DCA skip — price above 14d MA\n"
            f"Price: {btc_price:,.2f} EUR | 14d MA: {ma:,.2f} EUR\n"
            f"Pot: {state['accumulated_eur']:.2f} EUR | Days since last buy: {state['days_since_last_buy']}"
        )
        send_telegram(msg)

    save_state(state)


if __name__ == "__main__":
    run_dca()
    print_account_status()
