# Kraken Crypto Auto-Bot

Automated Python scripts to buy Bitcoin daily and withdraw it to a hardware wallet monthly.

## Features
* **buy_bitcoin.py**: Buys a set amount of BTC daily using Kraken API.
* **transfer_bitcoin_to_ledger.py**: Withdraws BTC to a whitelisted Ledger address monthly.
* **uv**: Modern, blazing-fast Python package management.
* **systemd**: Robust, automated task scheduling and logging.

## Local Setup (Laptop)

1. **Install uv:**
   curl -LsSf https://astral.sh/uv/install.sh | sh

2. **Initialize:**
   cd crypto_bot
   uv sync

3. **Configure Secrets:**
   Create a `.env` file with `KRAKEN_API_KEY`, `KRAKEN_API_SECRET`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID`.

## Server Setup (VM)

1. **Service Location:**
   Systemd files are located in `~/.config/systemd/user/`.

2. **Activate:**
   systemctl --user daemon-reload
   systemctl --user enable --now buy_bitcoin.timer
   systemctl --user enable --now withdraw_bitcoin.timer
   sudo loginctl enable-linger $USER

## Monitoring
* **View Timers:** systemctl --user list-timers
* **View Logs:** journalctl --user -u buy_bitcoin.service -f
* **Manual Run:** systemctl --user start buy_bitcoin.service
