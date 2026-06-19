"""Logging utility for Evergale BOT."""

import datetime
import logging
import logging.handlers
import os
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


def _attach_library_logging() -> None:
    """Route discord.py / voice_recv internal logs into app.log for diagnosis.

    Only active when ``RELAY_DEBUG`` is set. Surfaces receive-thread errors
    (decryption, opus decode, DAVE/E2EE) that the library would otherwise log
    to an unconfigured logger and that would never reach the operator.
    """
    if os.getenv("RELAY_DEBUG", "").strip().lower() in ("", "0", "false"):
        return
    lib_handler = logging.handlers.RotatingFileHandler(
        _LOG_FILE, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8",
    )
    lib_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
    discord_logger = logging.getLogger("discord")
    discord_logger.setLevel(logging.DEBUG)
    discord_logger.addHandler(lib_handler)


_attach_library_logging()


def log(message: str) -> None:
    """Log a formatted message with a timestamp to the console and rotating log file.

    Args:
        message: The message to log.
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{now}] 🤖 {message}"
    # Write to the rotating file first (UTF-8, always succeeds) so a console that
    # cannot encode the message never costs us the log line — or crashes the caller.
    _file_logger.info(formatted_msg)
    try:
        print(formatted_msg)
    except UnicodeEncodeError:
        print(formatted_msg.encode("ascii", "replace").decode("ascii"))
