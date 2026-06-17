"""Configuration and role helpers for Evergale BOT."""

import os
import re
from typing import ClassVar

import discord
from dotenv import load_dotenv

load_dotenv()

ROLE_EMOJI_IDS: dict[str, int] = {
    "Stonesplit Strength": 1512515618153304204,
    "Stonesplit Might": 1512515574402384113,
    "Silkbind Jade": 1512515536561377420,
    "Silkbind Deluge": 1512515502130331709,
    "Bellstrike Umbra": 1512515461135466546,
    "Bellstrike Splendor": 1512515406383026197,
    "Bamboocut Wind": 1512513817974800614,
    "Bamboocut Dust": 1512513402176798750,
    "no": 1503731711664455810,
    "multi": 1463334515605766249,
}


class Config:
    """Static configuration values for the bot."""

    GUILD_ID: ClassVar[int] = int(os.getenv("GUILD_ID", "0"))
    MAX_PURGE_SCAN: ClassVar[int] = 1_000
    RAID_HELPER_ID: ClassVar[int] = 579155972115660803
    EVENT_TAGS: ClassVar[list[str]] = [
        "<gvg_sat>",
        "<gvg_sun>",
        "<hero_realm>",
        "<group_pvp>",
        "<united_resolve>",
        "<speedrun>",
    ]
    CLEAN_EVENT_TAGS: ClassVar[frozenset[str]] = frozenset(
        t.replace("<", "").replace(">", "") for t in EVENT_TAGS
    )


def get_role_emoji(member: discord.Member | None) -> discord.PartialEmoji | None:
    """Return the custom emoji based on the member's highest relevant role.

    Args:
        member: The guild member to inspect, or None.

    Returns:
        A PartialEmoji for the member's role, the fallback "no" emoji, or None
        if member is None.
    """
    if not member:
        return None
    for role_name, emoji_id in ROLE_EMOJI_IDS.items():
        if role_name in ("no", "multi"):
            continue
        if any(re.search(role_name, role.name, flags=re.IGNORECASE) for role in member.roles):
            return discord.PartialEmoji(name=role_name.lower().replace(" ", "_"), id=emoji_id)
    return discord.PartialEmoji(name="n_", id=ROLE_EMOJI_IDS["no"])
