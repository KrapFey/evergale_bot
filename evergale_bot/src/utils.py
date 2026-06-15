"""Shared utility helpers for Evergale BOT."""

import datetime


def parse_utc_date(date_str: str, end_of_day: bool = False) -> int:
    """Parse a YYYY-MM-DD string to a UTC Unix timestamp.

    Args:
        date_str: Date string in YYYY-MM-DD format.
        end_of_day: If True, set time to 23:59 UTC.

    Returns:
        Unix timestamp as int.

    Raises:
        ValueError: If the format is invalid.
    """
    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=datetime.UTC)
    if end_of_day:
        dt = dt.replace(hour=23, minute=59)
    return int(dt.timestamp())
