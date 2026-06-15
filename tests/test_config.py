"""Tests for config helpers."""

from unittest.mock import MagicMock

import discord

from evergale_bot.src.config import ROLE_EMOJI_IDS, get_role_emoji


def _make_member(role_names: list[str]) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    roles = []
    for name in role_names:
        role = MagicMock()
        role.name = name
        roles.append(role)
    member.roles = roles
    return member


def test_none_member_returns_none():
    assert get_role_emoji(None) is None


def test_matching_role_returns_correct_emoji():
    member = _make_member(["Stonesplit Strength"])
    emoji = get_role_emoji(member)
    assert emoji is not None
    assert emoji.id == ROLE_EMOJI_IDS["Stonesplit Strength"]


def test_no_matching_role_returns_fallback():
    member = _make_member(["Some Random Role"])
    emoji = get_role_emoji(member)
    assert emoji is not None
    assert emoji.id == ROLE_EMOJI_IDS["no"]


def test_case_insensitive_match():
    member = _make_member(["stonesplit strength"])
    emoji = get_role_emoji(member)
    assert emoji is not None
    assert emoji.id == ROLE_EMOJI_IDS["Stonesplit Strength"]


def test_multi_key_not_matched_as_role():
    # "multi" and "no" are special keys, not real roles — they should not be matched
    member = _make_member(["multi"])
    emoji = get_role_emoji(member)
    assert emoji.id == ROLE_EMOJI_IDS["no"]
