# Kraken Crypto Auto-Bot

Automated Python scripts to buy Bitcoin daily, log purchases for tax reporting, and withdraw to a hardware wallet monthly.

## Features

- **buy_bitcoin.py** — Buys a set amount of BTC daily using the Kraken API. Uses a 14-day moving average to time purchases (defers when price is above MA, force-buys after 7 deferred days).
- **build_purchase_log.py** — Builds a `purchase_log.csv` with cost-basis data for each purchase, including fees and historical NOK/EUR exchange rates. Useful for reporting capital gains to Skatteetaten.
- **portfolio_summary.py** — Prints a snapshot of the current portfolio: holdings, cost basis, live value, unrealised P/L, price targets, and bot state.
- **portfolio_trend.py** — Compares BTC holdings over time against a hypothetical NOK savings account, DCA'd on the same schedule.
- **transfer_bitcoin_to_ledger.py** — Withdraws BTC to a whitelisted Ledger address monthly.
- **uv** — Modern, fast Python package management.
- **systemd** — Robust task scheduling. Unit files live in `systemd/` and are symlinked into place.

## Local Setup (Laptop)

1. **Install uv:**
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Initialize:**
   ```bash
   cd crypto_bot
   uv sync
   ```

3. **Configure secrets:**
   Copy `.env.example` to `.env` and fill in your values:
   ```bash
   cp .env.example .env
   ```

   Required variables:
   - `KRAKEN_API_KEY` / `KRAKEN_API_SECRET` — full trading permissions (for `buy_bitcoin.py`)
   - `KRAKEN_QUERY_API_KEY` / `KRAKEN_QUERY_API_SECRET` — read-only key, only needs "Query Closed Orders & Trades" permission (for `build_purchase_log.py`)
   - `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — for status and alert messages

## Server Setup (VM/LXC)

Systemd unit files are stored in `systemd/` in the repo. Symlink them into place so systemd can find them:

1. **Create symlinks:**
   ```bash
   cd ~/.config/systemd/user

   ln -s ~/src/crypto_bot/systemd/buy_bitcoin.service buy_bitcoin.service
   ln -s ~/src/crypto_bot/systemd/buy_bitcoin.timer buy_bitcoin.timer
   ln -s ~/src/crypto_bot/systemd/withdraw_bitcoin.service withdraw_bitcoin.service
   ln -s ~/src/crypto_bot/systemd/withdraw_bitcoin.timer withdraw_bitcoin.timer
   ln -s ~/src/crypto_bot/systemd/build_purchase_log.service build_purchase_log.service
   ln -s ~/src/crypto_bot/systemd/build_purchase_log.timer build_purchase_log.timer
   ```

2. **Enable and start timers:**
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now buy_bitcoin.timer
   systemctl --user enable --now withdraw_bitcoin.timer
   systemctl --user enable --now build_purchase_log.timer
   sudo loginctl enable-linger $USER
   ```

## Schedule

| Script | Time |
|---|---|
| `buy_bitcoin.py` | Daily at 12:00 |
| `build_purchase_log.py` | Daily at 12:15 |
| `transfer_bitcoin_to_ledger.py` | Monthly (see timer) |

## Purchase Log (Skatteetaten)

`build_purchase_log.py` maintains `purchase_log.csv` — a running record of every BTC purchase with all fields needed to calculate capital gains:

| Column | Description |
|---|---|
| `timestamp_utc` | Trade execution time |
| `txid` | Kraken trade ID |
| `btc_amount` | BTC received |
| `eur_amount` | EUR spent (excl. fee) |
| `fee_eur` | Kraken fee in EUR (tax-deductible) |
| `total_cost_eur` | EUR spent incl. fee |
| `btc_price_eur` | Actual executed price |
| `nok_per_eur` | Historical NOK/EUR rate on trade date |
| `btc_price_nok` | BTC price in NOK |
| `cost_basis_nok` | Total acquisition cost in NOK |

The CSV is gitignored (financial data). On first run, the full Kraken trade history is pulled and backfilled automatically.

## Portfolio Overview

Both scripts read `purchase_log.csv`, so run `build_purchase_log.py` at least once first.

**`portfolio_summary.py`** — a snapshot of where things stand right now: BTC held, total invested, live portfolio value, unrealised P/L, price targets for break-even/+25%/+50%/+100%, and the bot's current DCA/pot state.

```bash
uv run ./portfolio_summary.py          # both EUR and NOK
uv run ./portfolio_summary.py --nok    # NOK only
uv run ./portfolio_summary.py --eur    # EUR only
```

**`portfolio_trend.py`** — answers "would I have been better off in a savings account?" by comparing, at weekly points since your first purchase: cumulative NOK invested, BTC holdings' NOK value, and what those same NOK amounts would be worth had they been DCA'd into a savings account instead (default 4%/year). Writes `trend_data.json` (also gitignored) and prints it to stdout.

```bash
uv run ./portfolio_trend.py                  # default: 4% annual rate, weekly points
uv run ./portfolio_trend.py --rate 0.05      # use a different savings rate
uv run ./portfolio_trend.py --sample-days 1  # daily points instead of weekly
uv run ./portfolio_trend.py --no-live        # skip the live-price "today" point
```

## Monitoring

```bash
# List timers and next run times
systemctl --user list-timers

# Follow logs
journalctl --user -u buy_bitcoin.service -f
journalctl --user -u build_purchase_log.service -f

# Manual run
systemctl --user start buy_bitcoin.service
systemctl --user start build_purchase_log.service
```
