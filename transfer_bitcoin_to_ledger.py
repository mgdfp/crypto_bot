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
log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "withdraw_log.txt")
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

# Your Ledger Bitcoin address
LEDGER_BTC_ADDRESS = "Ledger Nano Wallet"
WITHDRAWAL_FEE = 0.0002  # Adjust this if Kraken updates their fee


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


def withdraw_to_ledger():
    try:
        # Get BTC balance
        balance = kraken.query_private("Balance")
        if "error" in balance and balance["error"]:
            logger.error("Error fetching balance: %s", balance["error"])
            # Send Telegram Message
            message = "Error fetching balance"
            response = requests.post(url, data={"chat_id": CHAT_ID, "text": message})
            return

        btc_balance = float(
            balance["result"].get("XXBT", 0)
        )  # Kraken stores BTC as 'XXBT'

        # Ensure there is enough BTC to cover the withdrawal fee
        if btc_balance <= WITHDRAWAL_FEE:
            logger.info("Not enough Bitcoin to withdraw after fees.")
            # Send Telegram Message
            message = "Not enough Bitcoin to withdraw after fees."
            response = requests.post(url, data={"chat_id": CHAT_ID, "text": message})
            return

        withdraw_amount = btc_balance - WITHDRAWAL_FEE
        nok_per_eur, btc_price_nok = fetch_exchange_rates()
        btc_balance_nok = btc_balance * btc_price_nok
        withdraw_amount_nok = withdraw_amount * btc_price_nok
        withdrawal_fee_nok = WITHDRAWAL_FEE * btc_price_nok

        logger.info(
            "Attempting to withdraw %.8f BTC (%.2f)to Ledger",
            withdraw_amount,
            withdraw_amount_nok,
        )
        # Send Telegram Message
        message1 = (
            "Kraken BTC Balance: {:.8f} BTC ({:.2f} NOK)\n"
            "Leaving {:.8f} BTC ({:.2f} NOK) for kraken fees\n"
            "Attempting to withdraw {:.8f} BTC ({:.2f} NOK) to Ledger."
        ).format(
            btc_balance,
            btc_balance_nok,
            WITHDRAWAL_FEE,
            withdrawal_fee_nok,
            withdraw_amount,
            withdraw_amount_nok,
        )

        # Withdraw request
        withdrawal = kraken.query_private(
            "Withdraw",
            {
                "asset": "XBT",
                "key": LEDGER_BTC_ADDRESS,
                "amount": str(withdraw_amount),
            },
        )

        if "error" in withdrawal and withdrawal["error"]:
            logger.error("Kraken API Error: %s", withdrawal["error"])
            # Send Telegram Message
            message2 = "Kraken API Error"
        else:
            logger.info("Withdrawal Successful: %s", withdrawal)
            # Send Telegram Message
            message2 = "Withdrawal Successful"
        # Send Telegram Message
        response = requests.post(
            url, data={"chat_id": CHAT_ID, "text": f"{message1}\n{message2}"}
        )
    except Exception as e:
        logger.error("Exception occurred: %s", str(e))


if __name__ == "__main__":
    withdraw_to_ledger()
