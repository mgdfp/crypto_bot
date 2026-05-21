"""portfolio_summary.py — BTC DCA portfolio overview."""

import argparse
import csv
import json
import logging
import os
from datetime import datetime, timezone

from common import kraken, fetch_exchange_rates

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PURCHASE_LOG = os.path.join(BASE_DIR, "purchase_log.csv")
DCA_STATE_FILE = os.path.join(BASE_DIR, "dca_state.json")

# Keep in sync with DAILY_BUY_EUR in buy_bitcoin.py
DAILY_BUY_EUR = 100

_log = logging.getLogger(__name__)
_log.addHandler(logging.StreamHandler())
_log.setLevel(logging.WARNING)


def load_purchases():
    if not os.path.exists(PURCHASE_LOG):
        print("purchase_log.csv not found — run build_purchase_log.py first.")
        return []
    with open(PURCHASE_LOG, newline="") as f:
        return list(csv.DictReader(f))


def get_live_btc_price_eur():
    ticker = kraken.query_public("Ticker", {"pair": "XXBTZEUR"})
    if ticker.get("error"):
        _log.error("Kraken ticker error: %s", ticker["error"])
        return None
    return float(ticker["result"]["XXBTZEUR"]["a"][0])


def get_kraken_eur_balance():
    resp = kraken.query_private("Balance")
    if resp.get("error"):
        _log.error("Kraken balance error: %s", resp["error"])
        return None
    return float(resp["result"].get("ZEUR", 0))


def load_dca_state():
    if os.path.exists(DCA_STATE_FILE):
        with open(DCA_STATE_FILE) as f:
            return json.load(f)
    return {"pot_eur": 0.0, "deferred_days": 0}


def target_status(current_price, target_price):
    pct = (target_price - current_price) / current_price * 100
    if pct > 0:
        return f"{pct:+.1f}% needed"
    return f"passed ({abs(pct):.1f}% above)"


def main():
    parser = argparse.ArgumentParser(description="BTC portfolio overview")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--nok", action="store_true", help="Show values in NOK only")
    group.add_argument("--eur", action="store_true", help="Show values in EUR only")
    args = parser.parse_args()

    show_eur = not args.nok
    show_nok = not args.eur

    rows = load_purchases()
    if not rows:
        return

    total_eur = sum(float(r["total_cost_eur"]) for r in rows)
    total_btc = sum(float(r["btc_amount"]) for r in rows)
    total_nok_paid = sum(float(r["cost_basis_nok"]) for r in rows)

    first_dt = datetime.strptime(
        rows[0]["timestamp_utc"], "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=timezone.utc)
    days_running = (datetime.now(timezone.utc) - first_dt).days

    nok_per_eur, _ = fetch_exchange_rates(_log)
    btc_price_eur = get_live_btc_price_eur()

    if btc_price_eur is None or nok_per_eur is None:
        print("Could not fetch live prices — check your connection.")
        return

    btc_price_nok = btc_price_eur * nok_per_eur
    value_eur = total_btc * btc_price_eur
    value_nok = value_eur * nok_per_eur
    pl_eur = value_eur - total_eur
    pl_nok = value_nok - total_nok_paid
    pl_pct_eur = pl_eur / total_eur * 100
    pl_pct_nok = pl_nok / total_nok_paid * 100

    # EUR targets use EUR cost basis; NOK targets use historical NOK cost basis
    avg_buy_eur = total_eur / total_btc
    avg_buy_nok = total_nok_paid / total_btc
    targets_eur = [total_eur * m / total_btc for m in (1.0, 1.25, 1.50, 2.00)]
    targets_nok = [total_nok_paid * m / total_btc for m in (1.0, 1.25, 1.50, 2.00)]
    target_labels = ["Break-even", "+25% return", "+50% return", "+100% return"]

    dca = load_dca_state()
    eur_balance = get_kraken_eur_balance()

    def line(label, eur_val, nok_val, suffix_eur="EUR", suffix_nok="NOK", sign=""):
        if show_eur:
            print(f"    {label:<16}: {sign}{eur_val:>12,.2f} {suffix_eur}")
        if show_nok:
            print(f"    {label:<16}: {sign}{nok_val:>12,.2f} {suffix_nok}")

    W = 48
    print()
    print(f"{'BTC Portfolio Summary':^{W}}")
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M'):^{W}}")
    print("-" * W)

    print(f"\n  Holdings")
    print(f"    BTC accumulated : {total_btc:.8f} BTC")
    print(f"    Purchases       : {len(rows)} over {days_running} days")

    print(f"\n  Cost basis")
    line("Total invested", total_eur, total_nok_paid)
    line("Avg buy price", avg_buy_eur, avg_buy_nok, "EUR/BTC", "NOK/BTC")

    price_line = []
    if show_eur:
        price_line.append(f"{btc_price_eur:,.2f} EUR/BTC")
    if show_nok:
        price_line.append(f"{btc_price_nok:,.2f} NOK/BTC")
    print(f"\n  Current value  (live: {' / '.join(price_line)})")
    line("Portfolio", value_eur, value_nok)

    sign_eur = "+" if pl_eur >= 0 else ""
    sign_nok = "+" if pl_nok >= 0 else ""
    if show_eur:
        print(f"    {'Unrealised P/L':<16}: {sign_eur}{pl_eur:>12,.2f} EUR  ({sign_eur}{pl_pct_eur:.1f}%)")
    if show_nok:
        print(f"    {'Unrealised P/L':<16}: {sign_nok}{pl_nok:>12,.2f} NOK  ({sign_nok}{pl_pct_nok:.1f}%)")

    print(f"\n  Price targets")
    for label, t_eur, t_nok in zip(target_labels, targets_eur, targets_nok):
        if show_eur:
            print(f"    {label:<16}: {t_eur:>10,.2f} EUR/BTC  {target_status(btc_price_eur, t_eur)}")
        if show_nok:
            print(f"    {label:<16}: {t_nok:>10,.2f} NOK/BTC  {target_status(btc_price_nok, t_nok)}")

    print(f"\n  Bot state")
    pot = dca["pot_eur"]
    deferred = dca["deferred_days"]
    pot_line = f"{pot:.2f} EUR"
    if deferred > 0:
        pot_line += f"  (deferred {deferred}/7 days)"
    print(f"    DCA pot         : {pot_line}")
    if eur_balance is not None:
        runway = f"~{eur_balance / DAILY_BUY_EUR:.0f} days"
        print(f"    Kraken EUR      : {eur_balance:,.2f} EUR  ({runway} runway)")
    print()


if __name__ == "__main__":
    main()
