"""Tests for RaidParser."""

from unittest.mock import MagicMock

from evergale_bot.src.roster import RaidParser


def _make_message(content: str = "", embeds: list | None = None) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.embeds = embeds or []
    return msg


def _make_embed(title: str | None = None, description: str | None = None,
                fields: list[tuple[str, str]] | None = None) -> MagicMock:
    embed = MagicMock()
    embed.title = title
    embed.description = description
    embed.fields = []
    for name, value in (fields or []):
        field = MagicMock()
        field.name = name
        field.value = value
        embed.fields.append(field)
    return embed


def test_parse_timestamp_extracted():
    embed = _make_embed(description="Event starts <t:1700000000:F>")
    msg = _make_message(embeds=[embed])
    result = RaidParser.parse(msg)
    assert result["timestamp"] == 1700000000


def test_parse_timestamp_missing():
    msg = _make_message(content="No timestamp here")
    result = RaidParser.parse(msg)
    assert result["timestamp"] == 0


def test_parse_accepted_and_maybe():
    body = (
        "Accepted (2)\n"
        "1. Alice\n"
        "2. Bob\n"
        "Maybe (1)\n"
        "1. Charlie\n"
        "Declined (1)\n"
        "1. Dave\n"
    )
    msg = _make_message(content=body)
    result = RaidParser.parse(msg)
    assert result["groups"]["Accepted"] == ["Alice", "Bob"]
    assert result["groups"]["Maybe"] == ["Charlie"]
    assert result["groups"]["Declined"] == ["Dave"]


def test_parse_no_signups():
    msg = _make_message(content="Nothing useful here")
    result = RaidParser.parse(msg)
    assert result["groups"]["Accepted"] == []
    assert result["groups"]["Maybe"] == []
    assert result["groups"]["Declined"] == []


def test_parse_unicode_whitespace_trimmed():
    # U+2800 (Braille blank) used by Raid-Helper as invisible padding at line boundaries
    body = "Accepted\n⠀1. Alice⠀\n"
    msg = _make_message(content=body)
    result = RaidParser.parse(msg)
    assert result["groups"]["Accepted"] == ["Alice"]


def test_parse_embeds_merged():
    embed = _make_embed(
        title="Guild Event <t:1700000001:F>",
        fields=[("Accepted (1)", "1. Zara"), ("Declined (0)", "")],
    )
    msg = _make_message(embeds=[embed])
    result = RaidParser.parse(msg)
    assert result["timestamp"] == 1700000001
    assert result["groups"]["Accepted"] == ["Zara"]
