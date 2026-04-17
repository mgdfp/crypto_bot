from common import kraken, send_telegram, setup_logger, fetch_exchange_rates

logger = setup_logger("withdraw_bitcoin", "withdraw_log.txt")

# Your Ledger Bitcoin address
LEDGER_BTC_ADDRESS = "Ledger Nano Wallet"
WITHDRAWAL_FEE = 0.0002  # Adjust this if Kraken updates their fee


def withdraw_to_ledger():
    try:
        # Get BTC balance
        balance = kraken.query_private("Balance")
        if "error" in balance and balance["error"]:
            logger.error("Error fetching balance: %s", balance["error"])
            send_telegram("Error fetching balance")
            return

        btc_balance = float(
            balance["result"].get("XXBT", 0)
        )  # Kraken stores BTC as 'XXBT'

        # Ensure there is enough BTC to cover the withdrawal fee
        if btc_balance <= WITHDRAWAL_FEE:
            logger.info("Not enough Bitcoin to withdraw after fees.")
            send_telegram("Not enough Bitcoin to withdraw after fees.")
            return

        withdraw_amount = btc_balance - WITHDRAWAL_FEE
        nok_per_eur, btc_price_nok = fetch_exchange_rates(logger)
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
            message2 = "Kraken API Error"
        else:
            logger.info("Withdrawal Successful: %s", withdrawal)
            message2 = "Withdrawal Successful"
        send_telegram(f"{message1}\n{message2}")
    except Exception as e:
        logger.error("Exception occurred: %s", str(e))


if __name__ == "__main__":
    withdraw_to_ledger()
