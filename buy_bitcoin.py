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

logger = logging.getLogger("MyLogger")
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

# Your daily BTC buy amount in EUR
DAILY_BUY_EUR = 15

# Maximum number of days to defer a buy before forcing it
MAX_DEFER_DAYS = 7

# Path to the DCA state file (tracks pot and defer streak)
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dca_state.json")


def load_dca_state():
    """Load DCA state from disk. Returns defaults if file doesn't exist."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Could not read DCA state file, using defaults: %s", e)
    return {"pot_eur": 0.0, "deferred_days": 0}


def save_dca_state(state):
    """Persist DCA state to disk."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    logger.info("DCA state saved: pot=%.2f EUR, deferred_days=%d", state["pot_eur"], state["deferred_days"])


def get_14day_ma():
    """Fetch daily OHLC data and compute the 14-day simple moving average of close prices."""
    ohlc = kraken.query_public("OHLC", {"pair": "XXBTZEUR", "interval": 1440})
    if "error" in ohlc and ohlc["error"]:
        logger.error("Error fetching OHLC data: %s", ohlc["error"])
        return None

    # OHLC entries: [time, open, high, low, close, vwap, volume, count]
    candles = ohlc["result"].get("XXBTZEUR", [])
    if len(candles) < 15:
        logger.error("Not enough OHLC data for MA calculation (got %d candles)", len(candles))
        return None

    # Use the last 14 closed candles (exclude the current in-progress candle at the end)
    closes = [float(c[4]) for c in candles[-15:-1]]
    ma = sum(closes) / len(closes)
    logger.info("14-day MA: %.2f EUR/BTC", ma)
    return ma


def get_btc_price():
    """Get the current BTC ask price in EUR."""
    ticker = kraken.query_public("Ticker", {"pair": "XXBTZEUR"})
    if "error" in ticker and ticker["error"]:
        logger.error("Error fetching BTC price: %s", ticker["error"])
        return None
    price = float(ticker["result"]["XXBTZEUR"]["a"][0])  # Ask price
    logger.info("Current BTC price: %.2f EUR/BTC", price)
    return price


def buy_bitcoin(amount_eur):
    """Place a market buy order for the given EUR amount."""
    btc_price = get_btc_price()
    if btc_price is None:
        logger.error("Failed to retrieve BTC price. Order canceled.")
        return False

    btc_amount = amount_eur / btc_price
    logger.info(
        "Attempting to buy %.8f BTC at %.2f EUR/BTC for %.2f EUR",
        btc_amount,
        btc_price,
        amount_eur,
    )

    try:
        order = kraken.query_private(
            "AddOrder",
            {
                "pair": "XXBTZEUR",
                "type": "buy",
                "ordertype": "market",
                "volume": str(btc_amount),
            },
        )
        if "error" in order and order["error"]:
            logger.error("Kraken API Error: %s", order["error"])
            send_telegram("BTC Purchase FAILED")
            return False
        else:
            logger.info("BTC Purchase Successful: %s", order)
            return True
    except Exception as e:
        logger.error("Exception occurred: %s", str(e))
        return False


def send_telegram(message):
    """Send a message to the configured Telegram chat."""
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": message})
    except Exception as e:
        logger.error("Failed to send Telegram message: %s", e)


def run_smart_dca():
    """
    Smart DCA with MA deferral.

    Each day:
    - Add DAILY_BUY_EUR to the pot.
    - If price < 14-day MA (or MA is unavailable): buy the entire pot now.
    - If price >= 14-day MA and deferred_days < MAX_DEFER_DAYS: hold, accumulate.
    - If deferred_days >= MAX_DEFER_DAYS: force-buy the entire pot regardless of price.

    After a buy (voluntary or forced), reset the pot to 0 and deferred_days to 0.
    """
    state = load_dca_state()
    state["pot_eur"] = round(state["pot_eur"] + DAILY_BUY_EUR, 2)
    pot = state["pot_eur"]
    deferred = state["deferred_days"]

    logger.info(
        "Smart DCA run: pot=%.2f EUR, deferred_days=%d, today=%s",
        pot, deferred, date.today().isoformat(),
    )

    btc_price = get_btc_price()
    ma_14d = get_14day_ma()

    # Determine whether to buy
    force_buy = deferred >= MAX_DEFER_DAYS
    price_below_ma = (btc_price is not None and ma_14d is not None and btc_price < ma_14d)
    ma_unavailable = btc_price is None or ma_14d is None

    if force_buy:
        reason = f"7-day maximum reached — force buying {pot:.2f} EUR"
        logger.info(reason)
        send_telegram(
            f"🔴 DCA Force Buy\n"
            f"Deferred for {deferred} days — buying now regardless of price.\n"
            f"Amount: {pot:.2f} EUR\n"
            f"BTC price: {btc_price:,.2f} EUR" if btc_price else f"Amount: {pot:.2f} EUR"
        )
        success = buy_bitcoin(pot)
        if success:
            state["pot_eur"] = 0.0
            state["deferred_days"] = 0
            send_telegram(
                f"✅ BTC Purchase Successful\n"
                f"Bought: {pot:.2f} EUR worth of BTC\n"
                f"BTC price: {btc_price:,.2f} EUR\n"
                f"14-day MA: {ma_14d:,.2f} EUR"
                if (btc_price and ma_14d) else f"✅ BTC Purchase Successful — {pot:.2f} EUR"
            )

    elif price_below_ma or ma_unavailable:
        if ma_unavailable:
            logger.warning("MA/price unavailable — buying anyway as fallback.")
        else:
            logger.info(
                "Price %.2f EUR < 14d MA %.2f EUR — buying %.2f EUR",
                btc_price, ma_14d, pot,
            )
        success = buy_bitcoin(pot)
        if success:
            state["pot_eur"] = 0.0
            state["deferred_days"] = 0
            msg_lines = [f"✅ BTC Purchase Successful", f"Amount: {pot:.2f} EUR"]
            if btc_price:
                msg_lines.append(f"BTC price: {btc_price:,.2f} EUR")
            if ma_14d:
                msg_lines.append(f"14-day MA: {ma_14d:,.2f} EUR")
                msg_lines.append(f"Price was {((ma_14d - btc_price) / ma_14d * 100):.1f}% below MA 📉")
            send_telegram("\n".join(msg_lines))

    else:
        # Price is at or above MA — defer
        state["deferred_days"] = deferred + 1
        days_left = MAX_DEFER_DAYS - state["deferred_days"]
        logger.info(
            "Price %.2f EUR >= 14d MA %.2f EUR — deferring. Pot: %.2f EUR, deferred_days: %d, force-buy in %d day(s).",
            btc_price, ma_14d, pot, state["deferred_days"], days_left,
        )
        send_telegram(
            f"⏳ DCA Deferred\n"
            f"BTC price ({btc_price:,.2f} EUR) is above 14-day MA ({ma_14d:,.2f} EUR).\n"
            f"Pot accumulated: {pot:.2f} EUR\n"
            f"Deferred {state['deferred_days']}/{MAX_DEFER_DAYS} days — force buy in {days_left} day(s)."
        )

    save_dca_state(state)


def fetch_exchange_rates():
    """
    Fetch the NOK/EUR exchange rate and calculate the NOK/BTC rate.
    Returns:
        tuple: (nok_per_eur, btc_price_nok) if successful, otherwise (None, None)
    """
    try:
        # Fetch NOK/EUR rate from the Frankfurter API
        response = requests.get("https://api.frankfurter.app/latest?from=NOK&to=EUR")
        data = response.json()
        if "rates" not in data or "EUR" not in data["rates"]:
            logger.error("Missing expected data in exchange rate response: %s", data)
            return None, None

        eur_rate = data["rates"]["EUR"]
        nok_per_eur = 1 / eur_rate  # Convert to NOK per EUR

        # Fetch BTC price in EUR from Kraken API
        ticker = kraken.query_public("Ticker", {"pair": "XXBTZEUR"})
        if "error" in ticker and ticker["error"]:
            logger.error("Error fetching BTC price: %s", ticker["error"])
            return nok_per_eur, None

        btc_price_eur = float(ticker["result"]["XXBTZEUR"]["a"][0])
        btc_price_nok = btc_price_eur * nok_per_eur  # Convert BTC price to NOK

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
    Log and print the account status including converted balances and
    estimated days until funds are exhausted based on daily spending.
    """
    nok_per_eur, btc_price_nok = fetch_exchange_rates()
    if nok_per_eur is None or btc_price_nok is None:
        logger.error("Could not fetch exchange rates. Skipping log update.")
        return

    eur_balance, btc_balance = fetch_kraken_balance()
    if eur_balance is None or btc_balance is None:
        logger.error("Could not fetch Kraken balance.")
        return

    ma_14d = get_14day_ma()
    state = load_dca_state()

    # Convert balances to NOK
    eur_balance_nok = eur_balance * nok_per_eur
    btc_balance_nok = btc_balance * btc_price_nok
    daily_buy_nok = DAILY_BUY_EUR * nok_per_eur

    # Calculate days left based on daily spending
    days_left = eur_balance / DAILY_BUY_EUR if eur_balance > 0 else 0

    logger.info("Kraken EUR Balance: %.2f EUR (%.2f NOK)", eur_balance, eur_balance_nok)
    logger.info("Kraken BTC Balance: %.8f BTC (%.2f NOK)", btc_balance, btc_balance_nok)
    logger.info("Daily BTC purchase: %.2f EUR (%.2f NOK)", DAILY_BUY_EUR, daily_buy_nok)
    logger.info("Kraken EUR Balance will be empty in: %.1f days.", days_left)

    ma_line = f"14-day MA: {ma_14d:,.2f} EUR/BTC\n" if ma_14d is not None else ""
    pot_line = (
        f"DCA pot: {state['pot_eur']:.2f} EUR (deferred {state['deferred_days']}/{MAX_DEFER_DAYS} days)\n"
        if state["deferred_days"] > 0
        else ""
    )

    # Send Telegram Message
    message = (
        "Kraken EUR Balance: {:.2f} EUR ({:.2f} NOK)\n"
        "Kraken BTC Balance: {:.8f} BTC ({:.2f} NOK)\n"
        "Daily BTC purchase: {:.2f} EUR ({:.2f} NOK)\n"
        "Kraken EUR Balance will be empty in: {:.1f} days.\n"
        "{ma_line}"
        "{pot_line}"
    ).format(
        eur_balance,
        eur_balance_nok,
        btc_balance,
        btc_balance_nok,
        DAILY_BUY_EUR,
        daily_buy_nok,
        days_left,
        ma_line=ma_line,
        pot_line=pot_line,
    )
    send_telegram(message)


if __name__ == "__main__":
    run_smart_dca()
    print_account_status()
