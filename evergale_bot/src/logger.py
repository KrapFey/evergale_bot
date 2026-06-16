"""Logging utility for Evergale BOT."""

import datetime
import logging
import logging.handlers
from pathlib import Path

_LOG_FILE: Path = Path("app.log")
_MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB per file
_BACKUP_COUNT: int = 3              # keep app.log, app.log.1, app.log.2, app.log.3

_handler: logging.handlers.RotatingFileHandler = logging.handlers.RotatingFileHandler(
    _LOG_FILE, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8",
)
_handler.setFormatter(logging.Formatter("%(message)s"))

_file_logger: logging.Logger = logging.getLogger("evergale_bot")
_file_logger.addHandler(_handler)
_file_logger.setLevel(logging.DEBUG)
_file_logger.propagate = False


def log(message: str) -> None:
    """Log a formatted message with a timestamp to the console and rotating log file.

    Args:
        message: The message to log.
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{now}] 🤖 {message}"
    print(formatted_msg)
    _file_logger.info(formatted_msg)
