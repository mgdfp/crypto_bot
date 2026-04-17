"""Shared utilities for the crypto bot scripts."""

import logging
from logging.handlers import RotatingFileHandler
import os

import krakenex
import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Kraken
# ---------------------------------------------------------------------------

API_KEY = os.getenv("KRAKEN_API_KEY")
API_SECRET = os.getenv("KRAKEN_API_SECRET")

kraken = krakenex.API(API_KEY, API_SECRET)

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
_TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


def send_telegram(message):
    """Send a message to the configured Telegram chat."""
    try:
        requests.post(_TELEGRAM_URL, data={"chat_id": CHAT_ID, "text": message})
    except Exception as e:
        logging.getLogger(__name__).error("Failed to send Telegram message: %s", e)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)


def setup_logger(name, log_filename):
    """Create a named logger with a rotating file handler.

    Args:
        name: Logger name (should be unique per script).
        log_filename: Filename inside the logs/ directory.

    Returns:
        A configured logging.Logger instance.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    handler = RotatingFileHandler(
        os.path.join(LOG_DIR, log_filename),
        maxBytes=1_000_000,
        backupCount=5,
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(handler)
    return logger


# ---------------------------------------------------------------------------
# Exchange rates
# ---------------------------------------------------------------------------


def fetch_exchange_rates(logger):
    """Fetch the NOK/EUR exchange rate and calculate the NOK/BTC rate.

    Args:
        logger: Logger instance for error reporting.

    Returns:
        tuple: (nok_per_eur, btc_price_nok) if successful, otherwise (None, None).
    """
    try:
        response = requests.get(
            "https://api.frankfurter.app/latest?from=NOK&to=EUR"
        )
        data = response.json()
        if "rates" not in data or "EUR" not in data["rates"]:
            logger.error(
                "Missing expected data in exchange rate response: %s", data
            )
            return None, None

        eur_rate = data["rates"]["EUR"]
        nok_per_eur = 1 / eur_rate

        ticker = kraken.query_public("Ticker", {"pair": "XXBTZEUR"})
        if "error" in ticker and ticker["error"]:
            logger.error("Error fetching BTC price: %s", ticker["error"])
            return nok_per_eur, None

        btc_price_eur = float(ticker["result"]["XXBTZEUR"]["a"][0])
        btc_price_nok = btc_price_eur * nok_per_eur

        return nok_per_eur, btc_price_nok

    except requests.exceptions.RequestException as e:
        logger.error("Network error fetching exchange rates: %s", e)
        return None, None
    except Exception as e:
        logger.error("Unexpected error fetching exchange rates: %s", e)
        return None, None
