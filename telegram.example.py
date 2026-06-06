"""Example Telegram configuration loader.

Copy the variable names into `.env` or your shell environment. Do not commit
real bot tokens or chat IDs.
"""

import os

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

if __name__ == "__main__":
    missing = [name for name, value in {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    }.items() if not value]
    if missing:
        print("Missing environment variables: " + ", ".join(missing))
    else:
        print("Telegram environment variables are configured.")
