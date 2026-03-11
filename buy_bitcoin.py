import krakenex
import logging
from logging.handlers import RotatingFileHandler
import requests
import os
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
log_file = "/home/morgan/src/crypto_bot/logs/btc_log.txt"
logger = logging.getLogger("MyLogger")
logger.setLevel(logging.INFO)

# Configure a rotating handler - up to 5 files of 1MB each
handler = RotatingFileHandler(
    log_file,
    maxBytes=1_000_000,  # 1 MB
    backupCount=5
)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

# Your daily BTC buy amount in EUR
DAILY_BUY_EUR = 15

# Function to get the current BTC price in EUR
def get_btc_price():
    ticker = kraken.query_public('Ticker', {'pair': 'XXBTZEUR'})
    if 'error' in ticker and ticker['error']:
        logger.error("Error fetching BTC price: %s", ticker['error'])
        return None
    price = float(ticker['result']['XXBTZEUR']['a'][0])  # Ask price
    logger.info("Current BTC price: %.2f EUR/BTC", price)
    return price

# Function to buy Bitcoin for a given amount in EUR
def buy_bitcoin(amount_eur):
    btc_price = get_btc_price()
    if btc_price is None:
        logger.error("Failed to retrieve BTC price. Order canceled.")
        return

    btc_amount = amount_eur / btc_price  # Convert EUR to BTC
    logger.info("Attempting to buy %.8f BTC at %.2f EUR/BTC for %.2f EUR", btc_amount, btc_price, amount_eur)

    try:
        order = kraken.query_private('AddOrder', {
            'pair': 'XXBTZEUR',
            'type': 'buy',
            'ordertype': 'market',
            'volume': str(btc_amount),
        })
        if 'error' in order and order['error']:
            logger.error("Kraken API Error: %s", order['error'])
            # Send Telegram Message
            message = ("BTC Purchase FAILED")
            response = requests.post(url, data={"chat_id": CHAT_ID, "text": message})
        else:
            logger.info("BTC Purchase Successful: %s", order)
            # Send Telegram Message
            message = ("BTC Purchase Successful")
            response = requests.post(url, data={"chat_id": CHAT_ID, "text": message})
    except Exception as e:
        logger.error("Exception occurred: %s", str(e))

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
        ticker = kraken.query_public('Ticker', {'pair': 'XXBTZEUR'})
        if 'error' in ticker and ticker['error']:
            logger.error("Error fetching BTC price: %s", ticker['error'])
            return nok_per_eur, None

        btc_price_eur = float(ticker['result']['XXBTZEUR']['a'][0])
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
    balance = kraken.query_private('Balance')
    if 'error' in balance and balance['error']:
        logger.error("Error fetching Kraken balance: %s", balance['error'])
        return None, None

    eur_balance = float(balance['result'].get('ZEUR', 0))
    btc_balance = float(balance['result'].get('XXBT', 0))
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
    # Send Telegram Message
    message = (
        "Kraken EUR Balance: {:.2f} EUR ({:.2f} NOK)\n"
        "Kraken BTC Balance: {:.8f} BTC ({:.2f} NOK)\n"
        "Daily BTC purchase: {:.2f} EUR ({:.2f} NOK)\n"
        "Kraken EUR Balance will be empty in: {:.1f} days."
    ).format(eur_balance, eur_balance_nok, 
             btc_balance, btc_balance_nok, 
             DAILY_BUY_EUR, daily_buy_nok, 
             days_left)
    # Send message
    response = requests.post(url, data={"chat_id": CHAT_ID, "text": message})




if __name__ == "__main__":
    buy_bitcoin(DAILY_BUY_EUR)
    print_account_status()
