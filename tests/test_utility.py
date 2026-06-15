"""Tests for utility date-parsing helpers."""

import datetime

import pytest

from evergale_bot.src.utils import parse_utc_date as _parse_utc_date


def test_valid_date_start_of_day():
    ts = _parse_utc_date("2024-01-15")
    expected = int(datetime.datetime(2024, 1, 15, 0, 0, tzinfo=datetime.UTC).timestamp())
    assert ts == expected


def test_valid_date_end_of_day():
    ts = _parse_utc_date("2024-01-15", end_of_day=True)
    expected = int(datetime.datetime(2024, 1, 15, 23, 59, tzinfo=datetime.UTC).timestamp())
    assert ts == expected


def test_invalid_format_raises():
    with pytest.raises(ValueError):
        _parse_utc_date("15-01-2024")


def test_invalid_string_raises():
    with pytest.raises(ValueError):
        _parse_utc_date("not-a-date")
