"""Utility command group for Evergale BOT."""

import asyncio
import contextlib
import datetime
import json
from collections.abc import Callable
from pathlib import Path

import discord
from discord import app_commands

from evergale_bot.src.config import Config
from evergale_bot.src.logger import log
from evergale_bot.src.roster import RaidParser
from evergale_bot.src.utils import parse_utc_date

_ParsedData = dict[str, int | dict[str, list[str]]]


def _save_report(msg_tag: str, parsed: _ParsedData) -> None:
    """Persist parsed attendance data to its JSON report file.

    Args:
        msg_tag: Clean event tag (e.g. ``gvg_sat``).
        parsed: Parsed message data containing ``timestamp`` and ``groups``.
    """
    report_file = Path(f"reports/{msg_tag}.json")
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_data: dict[str, object] = {}
    if report_file.exists():
        try:
            with (report_file.open("r", encoding="utf-8") as f,
                  contextlib.suppress(json.JSONDecodeError)):
                report_data = json.load(f)
        except OSError:
            pass
    report_data[str(parsed["timestamp"])] = parsed["groups"]
    with report_file.open("w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=4)


async def _forward_messages(messages: list[tuple[discord.Message, _ParsedData, str | None]],
                             destination: discord.TextChannel) -> tuple[int, int]:
    """Forward and delete matched raid messages to the destination channel.

    Args:
        messages: List of (message, parsed_data, tag) tuples in chronological order.
        destination: Target channel for forwarded messages.

    Returns:
        Tuple of (archived_count, failed_count).
    """
    archived, failed = 0, 0
    for msg, parsed, msg_tag in reversed(messages):
        if msg_tag:
            file_tag = msg_tag.replace("<", "").replace(">", "")
            _save_report(file_tag, parsed)
        try:
            await msg.forward(destination)
            await msg.delete()
            archived += 1
            await asyncio.sleep(1)
        except discord.HTTPException:
            failed += 1
    return archived, failed


def _match_tag(msg: discord.Message, tag: str | None) -> str | None:
    """Find which event tag this message belongs to.

    Args:
        msg: The Raid-Helper message to inspect.
        tag: Specific tag to check for, or None to check all known tags.

    Returns:
        The matched tag string, or None if no match found.
    """
    if tag:
        return tag if any(tag.lower() in str(e.to_dict()).lower() for e in msg.embeds) else None
    for t in Config.EVENT_TAGS:
        if any(t.lower() in str(e.to_dict()).lower() for e in msg.embeds):
            return t
    return None


class Utility(app_commands.Group, name="utility", description="General utility commands"):
    """Slash command group for channel maintenance utilities."""

    async def __manual_delete(self, channel: discord.TextChannel,
                               check: Callable[[discord.Message], bool], limit: int) -> int:
        """Fall back to individual message deletion for messages older than 14 days.

        Args:
            channel: The channel to scan.
            check: Callable that returns True for messages that should be deleted.
            limit: Max messages to scan.

        Returns:
            Count of successfully deleted messages.
        """
        log("[CLEAN] Bulk purge unavailable, switching to manual scan")
        tasks = []
        try:
            async for msg in channel.history(limit=limit):
                if check(msg):
                    tasks.append(msg.delete())
        except discord.HTTPException:
            log("[CLEAN] Scan failed")
            return 0
        if not tasks:
            return 0
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return sum(1 for r in results if not isinstance(r, Exception))

    async def __do_clean(self, channel: discord.TextChannel, target: str,
                         limit: int, user: discord.Member | None) -> int:
        """Execute the purge and return the number of deleted messages.

        Args:
            channel: The channel to purge.
            target: Deletion target type.
            limit: Max messages to scan.
            user: Specific member to target (only used when target is ``users``).

        Returns:
            Count of deleted messages.
        """
        def check(msg: discord.Message) -> bool:
            if target == "all":
                return True
            if target == "bots":
                return msg.author.bot
            if target == "users":
                if user:
                    return msg.author.id == user.id
                return not msg.author.bot
            return False

        try:
            deleted = await channel.purge(limit=limit, check=check, bulk=True)
            return len(deleted)
        except discord.Forbidden:
            raise
        except discord.HTTPException:
            return await self.__manual_delete(channel, check, limit)

    @app_commands.command(name="clean", description="Clean channel")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        target="Which messages to remove: all | bots | users",
        limit="How many messages to scan (max 1000)",
        user="When target=users, target this member",
    )
    @app_commands.choices(target=[
        app_commands.Choice(name="all", value="all"),
        app_commands.Choice(name="bots", value="bots"),
        app_commands.Choice(name="users", value="users"),
    ])
    async def clean(self, interaction: discord.Interaction, target: str = "all",
                         limit: int = 100, user: discord.Member | None = None) -> None:
        """Delete messages from the current channel by target type.

        Args:
            interaction: The Discord interaction context.
            target: Which messages to remove (``all``, ``bots``, or ``users``).
            limit: How many recent messages to scan (capped at MAX_PURGE_SCAN).
            user: When target is ``users``, restrict deletion to this member.
        """
        channel = getattr(interaction, "channel", None)
        channel_name = getattr(channel, "name", "unknown-channel")
        log(f"[CLEAN] Clean requested by @{interaction.user.display_name} in #{channel_name} "
            f"-> {target}, {limit} messages")
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("You need Manage Messages permission.",
                                                    ephemeral=True)
            return
        bot_member = interaction.guild.me
        if not bot_member or not bot_member.guild_permissions.manage_messages:
            await interaction.response.send_message("I need Manage Messages permission.",
                                                    ephemeral=True)
            return
        limit = max(1, min(limit, Config.MAX_PURGE_SCAN))
        await interaction.response.defer(ephemeral=True)
        try:
            deleted_count = await self.__do_clean(channel, target, limit, user)
        except discord.Forbidden:
            await interaction.followup.send("I don't have permission to delete here.",
                                            ephemeral=True)
            return
        log(f"[CLEAN] Clean complete -> {deleted_count} messages")
        await interaction.followup.send(
            f"✅ Clean complete — removed **{deleted_count}** msgs (target: **{target}**).",
            ephemeral=True,
        )

    async def __collect_messages(self, source: discord.TextChannel, tag: str | None,
                                  start_ts: int, end_ts: int, archive_limit: int,
                                  scan_limit: int,
                                  ) -> list[tuple[discord.Message, _ParsedData, str | None]]:
        """Scan source channel and collect matching Raid-Helper messages.

        Args:
            source: Channel to scan.
            tag: Optional event tag filter.
            start_ts: Inclusive start UTC timestamp.
            end_ts: Inclusive end UTC timestamp.
            archive_limit: Max messages to collect.
            scan_limit: Max messages to scan.

        Returns:
            List of (message, parsed_data, matched_tag) tuples.
        """
        results: list[tuple[discord.Message, _ParsedData, str | None]] = []
        async for msg in source.history(limit=scan_limit):
            if msg.author.id != Config.RAID_HELPER_ID:
                continue
            parsed = RaidParser.parse(msg)
            if not (start_ts <= parsed["timestamp"] <= end_ts):
                continue
            msg_tag = _match_tag(msg, tag)
            if tag and msg_tag is None:
                continue
            results.append((msg, parsed, msg_tag))
            if len(results) >= archive_limit:
                break
        return results

    @app_commands.command(name="archive", description="Archive raids and save attendance")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        source="The channel to search for the Raid-Helper messages",
        destination="The channel to move the messages to",
        tag="Optional: Specific event tag to archive. Leave blank for ALL.",
        start_date="Optional: Archive events after this date (YYYY-MM-DD)",
        end_date="Optional: Archive events before this date (YYYY-MM-DD)",
        archive_limit="Max matched messages to archive (default 50)",
        scan_limit="How many messages back to search overall (default 200)",
    )
    @app_commands.choices(tag=[
        app_commands.Choice(name=t, value=t) for t in Config.EVENT_TAGS
    ])
    async def archive(self, interaction: discord.Interaction,
                           source: discord.TextChannel, destination: discord.TextChannel,
                           tag: str | None = None, start_date: str | None = None,
                           end_date: str | None = None, archive_limit: int = 50,
                           scan_limit: int = 200) -> None:
        """Archive Raid-Helper messages with optional tag and date filtering.

        Args:
            interaction: The Discord interaction context.
            source: Channel to search for Raid-Helper messages.
            destination: Channel to move the archived messages to.
            tag: Optional event tag filter.
            start_date: Optional start date filter (YYYY-MM-DD, UTC).
            end_date: Optional end date filter (YYYY-MM-DD, UTC).
            archive_limit: Maximum number of matched messages to archive.
            scan_limit: How many messages back to scan overall.
        """
        await interaction.response.defer(ephemeral=True)
        try:
            start_ts = parse_utc_date(start_date) if start_date else 0
            end_ts = (parse_utc_date(end_date, end_of_day=True) if end_date
                      else int(datetime.datetime.now(tz=datetime.UTC).timestamp()))
        except ValueError:
            await interaction.followup.send("❌ Use `YYYY-MM-DD` format for dates.",
                                            ephemeral=True)
            return
        log(f"[ARCHIVE] Archive requested by @{interaction.user.display_name} -> {tag or 'ALL'}")
        target_messages = await self.__collect_messages(source, tag, start_ts, end_ts,
                                                        archive_limit, scan_limit)
        if not target_messages:
            await interaction.followup.send("No matching messages found.", ephemeral=True)
            return
        archived_count, failed_count = await _forward_messages(target_messages, destination)
        suffix = f" ({failed_count} failed)" if failed_count else ""
        await interaction.followup.send(
            f"✅ Archived **{archived_count}** messages to {destination.mention}.{suffix}",
            ephemeral=True,
        )
