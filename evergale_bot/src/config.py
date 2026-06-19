"""Configuration and role helpers for Evergale BOT."""

import os
import re
from typing import ClassVar

import discord
from dotenv import load_dotenv

load_dotenv()

ROLE_EMOJI_IDS: dict[str, int] = {
    "Stonesplit Strength": 1512524324026712074,
    "Stonesplit Might": 1512524267177377873,
    "Silkbind Jade": 1512524216635752478,
    "Silkbind Deluge": 1512524156376322078,
    "Bellstrike Umbra": 1512524090374619267,
    "Bellstrike Splendor": 1512524043574575124,
    "Bamboocut Wind": 1512523965959110707,
    "Bamboocut Dust": 1512523858496983060,
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
