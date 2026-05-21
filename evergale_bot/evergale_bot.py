"""Evergale BOT — utilities for cleaning channels and archiving Raid-Helper events."""

import asyncio
import datetime
import os
import sys

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

INTENTS = discord.Intents.default()
INTENTS.guilds = True
INTENTS.members = True
INTENTS.messages = True
INTENTS.message_content = True

BOT = commands.Bot(command_prefix="!", intents=INTENTS)


def log(message: str) -> None:
    """Helper to print nicely formatted and timestamped console logs."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] 🤖 {message}")


class Config:
    """Static configuration values."""

    GUILD_ID: int = int(os.getenv("GUILD_ID", 0))
    MAX_PURGE_SCAN: int = 1000
    RAID_HELPER_ID: int = 579155972115660803


class Cleaner:
    """Channel cleaning utilities exposed as a slash command."""

    @staticmethod
    async def clean_channel(interaction: discord.Interaction, filter_value: str = "all",
                            limit: int = 100, user: discord.Member | None = None) -> None:
        """Clean messages in the current channel with filters and limit."""
        channel = getattr(interaction, "channel", None)
        channel_name = getattr(channel, "name", "unknown-channel")

        log(f"[CLEAN] Initiated by @{interaction.user.name} in #{channel_name} "
            f"(Filter: {filter_value}, Limit: {limit}, User: {getattr(user, 'name', 'None')})")

        if not interaction.user.guild_permissions.manage_messages:
            log(f"[CLEAN] Failed: @{interaction.user.name} lacks Manage Messages permission.")
            await interaction.response.send_message("You need Manage Messages permission "
                                                    "to use this.", ephemeral=True)
            return

        bot_member = interaction.guild.me
        if not bot_member or not bot_member.guild_permissions.manage_messages:
            log("[CLEAN] Failed: Bot lacks Manage Messages permission.")
            await interaction.response.send_message("I need Manage Messages permission "
                                                    "to delete messages.", ephemeral=True)
            return

        filter_value = filter_value.lower()
        if filter_value not in ("all", "bots", "user"):
            await interaction.response.send_message("Invalid filter. Use `all`, `bots`, or `user`.",
                                                    ephemeral=True)
            return

        limit = max(1, min(limit, Config.MAX_PURGE_SCAN))
        await interaction.response.defer(ephemeral=True)

        def check(msg: discord.Message) -> bool:
            if msg.id == interaction.id:
                return False
            if filter_value == "all":
                return True
            if filter_value == "bots":
                return msg.author.bot
            if filter_value == "user":
                return user is not None and msg.author.id == user.id
            return False

        deleted_count = 0
        try:
            log(f"[CLEAN] Purging up to {limit} recent messages...")
            deleted = await channel.purge(limit=limit, check=check, bulk=True)
            deleted_count += len(deleted)
        except discord.Forbidden:
            log("[CLEAN] Failed: Bot forbidden from deleting in this channel.")
            await interaction.followup.send("I don't have permission to delete messages "
                                            "in this channel.", ephemeral=True)
            return
        except discord.HTTPException:
            # fall back to manual deletion
            log("[CLEAN] Bulk purge failed, falling back to manual history scan.")

        remaining: list[discord.Message] = []
        cutoff = discord.utils.utcnow() - datetime.timedelta(days=14)
        tasks = []

        try:
            async for msg in channel.history(limit=limit):
                if not check(msg):
                    continue
                if msg.created_at <= cutoff:
                    remaining.append(msg)
                else:
                    # Collect tasks to delete concurrently
                    tasks.append(msg.delete())

        except Exception as e:
            log(f"[CLEAN] Failed history scan: {e}")
            await interaction.followup.send("Failed to scan channel history. "
                                            "Check bot permissions.", ephemeral=True)
            return

        # Add remaining >14d old messages to the deletion task pool
        tasks.extend([old_msg.delete() for old_msg in remaining])

        # Delete messages concurrently instead of sequentially
        if tasks:
            log(f"[CLEAN] Concurrently deleting {len(tasks)} manual messages...")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            deleted_count += sum(1 for r in results if not isinstance(r, Exception))

        log(f"[CLEAN] Success: Removed {deleted_count} messages.")
        await interaction.followup.send(
            f"Clean complete — removed **{deleted_count}** messages (filter: **{filter_value}**).",
            ephemeral=True,
        )


# Register commands and events


@BOT.event
async def on_ready() -> None:
    """Sync commands cleanly to your specific server."""
    log(f"Logged in as {BOT.user.name} (ID: {BOT.user.id})")

    guild = discord.Object(id=Config.GUILD_ID)

    BOT.tree.copy_global_to(guild=guild)
    synced = await BOT.tree.sync(guild=guild)

    # Explicitly overwrite global commands without wiping internal memory
    await BOT.tree.sync(guild=None)

    log(f"Synced {len(synced)} active commands to guild {Config.GUILD_ID}")


@BOT.tree.command(name="clean",
                  description="Clean messages in this channel (filters: all, bots, user)")
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.describe(
    filter="Which messages to remove: all | bots | user",
    limit="How many messages to scan (max 1000)",
    user="When filter=user, target this member",
)
async def clean_cmd(interaction: discord.Interaction, filter: str = "all", limit: int = 100,  # noqa: A002
                    user: discord.Member | None = None) -> None:
    """Handles the /clean command."""
    await Cleaner.clean_channel(interaction, filter_value=filter, limit=limit, user=user)


@BOT.tree.command(name="archive-raid",
                  description="Forward Raid-Helper embeds to another channel and delete originals")
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.describe(
    source="The channel to search for the Raid-Helper messages",
    destination="The channel to move the messages to",
    tag="Optional tag to look for inside the embed (e.g. #sun_gw)",
    archive_limit="Max matched messages to archive (default 50)",
    scan_limit="How many messages back to search overall (default 200)",
)
async def archive_raid_cmd(interaction: discord.Interaction,
                           source: discord.TextChannel,
                           destination: discord.TextChannel,
                           tag: str | None = None,
                           archive_limit: int = 50,
                           scan_limit: int = 200) -> None:
    """Finds multiple Raid-Helper messages, forwards them, and deletes the originals."""
    log(f"[ARCHIVE] Initiated by @{interaction.user.name} | From: #{source.name} -> "
        f"To: #{destination.name} "
        f"| Tag: {tag} | Max Archive: {archive_limit} | Scan depth: {scan_limit}")

    await interaction.response.defer(ephemeral=True)

    if not interaction.user.guild_permissions.manage_messages:
        log(f"[ARCHIVE] Failed: @{interaction.user.name} lacks Manage Messages permission.")
        await interaction.followup.send(
            "You need Manage Messages permission to use this.",
            ephemeral=True,
        )
        return

    target_messages: list[discord.Message] = []

    try:
        log(f"[ARCHIVE] Scanning {scan_limit} messages in #{source.name}...")
        async for msg in source.history(limit=scan_limit):
            if msg.author.id == Config.RAID_HELPER_ID:

                if tag:
                    tag_found = False
                    for embed in msg.embeds:
                        if tag.lower() in str(embed.to_dict()).lower():
                            tag_found = True
                            break

                    if not tag_found:
                        continue

                target_messages.append(msg)
                if len(target_messages) >= archive_limit:
                    break

    except discord.Forbidden:
        log(f"[ARCHIVE] Failed: Bot cannot read history in #{source.name}.")
        await interaction.followup.send(
            f"I don't have permission to read message history in {source.mention}.",
            ephemeral=True,
        )
        return

    if not target_messages:
        log("[ARCHIVE] Success/Empty: No matching Raid-Helper messages found.")
        msg_suffix = f" containing the tag `{tag}`" if tag else ""
        await interaction.followup.send(
            f"Could not find any Raid-Helper messages{msg_suffix} in the last {scan_limit} "
            f"messages of {source.mention}.",
            ephemeral=True,
        )
        return

    target_messages.reverse()
    archived_count = 0
    failed_count = 0

    log(f"[ARCHIVE] Starting to forward and delete {len(target_messages)} messages...")

    for msg in target_messages:
        try:
            await msg.forward(destination)
            await msg.delete()
            archived_count += 1
            await asyncio.sleep(1)
        except discord.HTTPException as e:
            log(f"[ARCHIVE] Warning: Failed to move message ID {msg.id}: {e}")
            failed_count += 1
            continue

    log(f"[ARCHIVE] Complete: {archived_count} moved successfully, {failed_count} failed.")

    fail_text = f" ({failed_count} failed due to API errors)" if failed_count > 0 else ""
    await interaction.followup.send(
        f"**Archive Complete!**\n"
        f"Moved **{archived_count}** Raid-Helper messages to {destination.mention}.{fail_text}",
        ephemeral=True,
    )


def main() -> int:
    """Load environment and run the bot."""
    token = os.getenv("MAGIC")
    if not token:
        print("Missing MAGIC token in environment variables.")
        return 1
    BOT.run(token, log_handler=None) # Disables standard discord logs to keep console clean
    return 0


if __name__ == "__main__":
    sys.exit(main())
