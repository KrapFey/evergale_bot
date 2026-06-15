"""Logging utility for Evergale BOT."""

import datetime
from pathlib import Path


def log(message: str) -> None:
    """Log a formatted message with a timestamp to the console and local log file.

    Args:
        message: The message to log.
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{now}] 🤖 {message}"
    print(formatted_msg)
    with Path("app.log").open("a", encoding="utf-8") as log_file:
        log_file.write(formatted_msg + "\n")
