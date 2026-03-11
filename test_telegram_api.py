import requests
import os
from dotenv import load_dotenv

load_dotenv()

# Replace with your bot token
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Replace with your chat ID
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Message to send
MESSAGE = "Hello, this is a test message from my Python script!"

# Telegram API URL
url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

# Send message
response = requests.post(url, data={"chat_id": CHAT_ID, "text": MESSAGE})

# Print response
print(response.json())
