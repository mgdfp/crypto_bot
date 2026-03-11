# Kraken Crypto Auto-Bot

Automated Python scripts to buy Bitcoin daily and withdraw it to a hardware wallet monthly.

## Features
* **buy_bitcoin.py**: Buys a set amount of BTC daily using the Kraken API. Calculates balances in EUR, BTC, and NOK, and sends a Telegram status update.
* **transfer_bitcoin_to_ledger.py**: Withdraws accumulated BTC to a whitelisted Ledger address on Kraken.
* **Telegram Alerts**: Sends execution summaries, errors, and account balances via a Telegram bot.

## Setup

1. **Clone the repository:**
   git clone git@github.com:mgdfp/crypto_bot.git
   cd crypto_bot

2. **Set up the virtual environment:**
   python3 -m venv .venv
   source .venv/bin/activate
   pip install krakenex requests python-dotenv

3. **Configure Secrets:**
   Create a `.env` file in the root directory and add your API keys:
   KRAKEN_API_KEY=your_api_key
   KRAKEN_API_SECRET=your_api_secret
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=your_chat_id

   Secure this file by running `chmod 600 .env`.

4. **Create the log folder:**
   mkdir logs

## Automation (Cron)
Add to your crontab (`crontab -e`):

# Buy Bitcoin daily at 12:00 PM
0 12 * * * cd /home/username/src/crypto_bot && /home/username/src/crypto_bot/.venv/bin/python3 buy_bitcoin.py >> /home/username/src/crypto_bot/logs/btc_log.txt 2>&1

# Withdraw to Ledger on the 1st of every month at 12:05 PM
5 12 1 * * cd /home/username/src/crypto_bot && /home/username/src/crypto_bot/.venv/bin/python3 transfer_bitcoin_to_ledger.py >> /home/username/src/crypto_bot/logs/withdraw_log.txt 2>&1

