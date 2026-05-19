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
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("You need Manage Messages permission "
                                                    "to use this.", ephemeral=True)
            return

        bot_member = interaction.guild.me
        if not bot_member or not bot_member.guild_permissions.manage_messages:
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

        channel: discord.TextChannel = interaction.channel  # type: ignore[assignment]

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
            deleted = await channel.purge(limit=limit, check=check, bulk=True)
            deleted_count += len(deleted)
        except discord.Forbidden:
            await interaction.followup.send("I don't have permission to delete messages "
                                            "in this channel.", ephemeral=True)
            return
        except discord.HTTPException:
            # fall back to manual deletion
            pass

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

        except Exception:
            await interaction.followup.send("Failed to scan channel history. "
                                            "Check bot permissions.", ephemeral=True)
            return

        # Add remaining >14d old messages to the deletion task pool
        tasks.extend([old_msg.delete() for old_msg in remaining])

        # Delete messages concurrently instead of sequentially
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            deleted_count += sum(1 for r in results if not isinstance(r, Exception))

        await interaction.followup.send(
            f"Clean complete — removed **{deleted_count}** messages (filter: **{filter_value}**).",
            ephemeral=True,
        )


# Register commands and events


@BOT.event
async def on_ready() -> None:
    """Sync commands to the configured guild on ready (instant visibility)."""
    guild = discord.Object(id=Config.GUILD_ID)
    BOT.tree.copy_global_to(guild=guild)
    synced = await BOT.tree.sync(guild=guild)
    print(f"Synced {len(synced)} commands to guild {Config.GUILD_ID}")


@BOT.tree.command(name="clean",
                  description="Clean messages in this channel (filters: all, bots, user)")
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
                  description="Forward a Raid-Helper embed to another channel and delete original")
@discord.app_commands.describe(
    source="The channel to search for the Raid-Helper message",
    destination="The channel to move the message to",
    limit="How many messages back to search (default 50)",
    tag="Optional tag to look for inside the embed (e.g. #sun_gw)",
)
async def archive_raid_cmd(interaction: discord.Interaction,
                           source: discord.TextChannel,
                           destination: discord.TextChannel,
                           limit: int = 50,
                           tag: str | None = None) -> None:
    """Finds a Raid-Helper message (optionally by tag), forwards it, and deletes the original."""
    await interaction.response.defer(ephemeral=True)

    # Enforce permissions
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.followup.send(
            "You need Manage Messages permission to use this.",
            ephemeral=True,
        )
        return

    target_msg = None
    try:
        # Scan the source channel's history for the bot
        async for msg in source.history(limit=limit):
            if msg.author.id == Config.RAID_HELPER_ID:

                # If a tag was provided, verify it exists inside the embed
                if tag:
                    tag_found = False
                    for embed in msg.embeds:
                        # Convert dict to string for a quick deep search of all embed text
                        if tag.lower() in str(embed.to_dict()).lower():
                            tag_found = True
                            break

                    if not tag_found:
                        continue  # Skip this message, keep searching older ones

                target_msg = msg
                break
    except discord.Forbidden:
        await interaction.followup.send(
            f"I don't have permission to read message history in {source.mention}.",
            ephemeral=True,
        )
        return

    if not target_msg:
        msg_suffix = f" containing the tag `{tag}`" if tag else ""
        await interaction.followup.send(
            f"Could not find a Raid-Helper message{msg_suffix} in the last {limit} "
            f"messages of {source.mention}.",
            ephemeral=True,
        )
        return

    try:
        # Forward the message natively
        await target_msg.forward(destination)

        # Delete the original message
        await target_msg.delete()

        await interaction.followup.send(
            f"Successfully forwarded the Raid-Helper archive to {destination.mention} and "
            "removed the original.",
            ephemeral=True,
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "I lack permissions to either forward messages to the destination channel "
            "or delete the original message.",
            ephemeral=True,
        )
    except discord.HTTPException as e:
        await interaction.followup.send(f"An API error occurred: {e}", ephemeral=True)


def main() -> int:
    """Load environment and run the bot."""
    token = os.getenv("MAGIC")
    if not token:
        print("Missing MAGIC token in environment variables.")
        return 1
    BOT.run(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
