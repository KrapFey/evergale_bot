"""Evergale BOT — utilities for cleaning channels and archiving Raid-Helper events."""

import asyncio
import datetime
import os
import re
import sys
from pathlib import Path

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
    formatted_msg = f"[{now}] 🤖 {message}"
    print(formatted_msg)
    with Path("app.log").open("a", encoding="utf-8") as f:
            f.write(formatted_msg + "\n")


class Config:
    """Static configuration values."""

    GUILD_ID: int = int(os.getenv("GUILD_ID", 0))
    MAX_PURGE_SCAN: int = 1000
    RAID_HELPER_ID: int = 579155972115660803


class Cleaner:
    """Channel cleaning utilities exposed as a slash command."""

    @staticmethod
    async def clean_channel(
        interaction: discord.Interaction,
        filter_value: str = "all",
        limit: int = 100,
        user: discord.Member | None = None,
    ) -> None:
        """Clean messages in the current channel with filters and limit."""
        channel = getattr(interaction, "channel", None)
        channel_name = getattr(channel, "name", "unknown-channel")

        log(
            f"[CLEAN] Initiated by @{interaction.user.display_name} in #{channel_name} "
            f"(Filter: {filter_value}, Limit: {limit}, "
            f"User: {getattr(user, 'name', 'None')})",
        )

        if not interaction.user.guild_permissions.manage_messages:
            log(f"[CLEAN] Failed: @{interaction.user.display_name} lacks Manage Messages perm.")
            await interaction.response.send_message(
                "You need Manage Messages permission to use this.", ephemeral=True,
            )
            return

        bot_member = interaction.guild.me
        if not bot_member or not bot_member.guild_permissions.manage_messages:
            log("[CLEAN] Failed: Bot lacks Manage Messages permission.")
            await interaction.response.send_message(
                "I need Manage Messages permission to delete messages.", ephemeral=True,
            )
            return

        filter_value = filter_value.lower()
        if filter_value not in ("all", "bots", "user"):
            await interaction.response.send_message(
                "Invalid filter. Use `all`, `bots`, or `user`.", ephemeral=True,
            )
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
            await interaction.followup.send(
                "I don't have permission to delete messages in this channel.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
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
                    tasks.append(msg.delete())
        except Exception as e:
            log(f"[CLEAN] Failed history scan: {e}")
            await interaction.followup.send(
                "Failed to scan channel history. Check bot permissions.", ephemeral=True,
            )
            return

        tasks.extend([old_msg.delete() for old_msg in remaining])

        if tasks:
            log(f"[CLEAN] Concurrently deleting {len(tasks)} manual messages...")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            deleted_count += sum(1 for r in results if not isinstance(r, Exception))

        log(f"[CLEAN] Success: Removed {deleted_count} messages.")
        await interaction.followup.send(
            f"Clean complete — removed **{deleted_count}** msgs (filter: **{filter_value}**).",
            ephemeral=True,
        )


# Register commands and events

@BOT.event
async def on_ready() -> None:
    """Sync commands cleanly to your specific server."""
    log(f"Logged in as {BOT.user.display_name} (ID: {BOT.user.id})")

    guild = discord.Object(id=Config.GUILD_ID)

    BOT.tree.copy_global_to(guild=guild)
    synced = await BOT.tree.sync(guild=guild)

    # Explicitly overwrite global commands without wiping internal memory
    await BOT.tree.sync(guild=None)

    log(f"Synced {len(synced)} active commands to guild {Config.GUILD_ID}")


@BOT.tree.command(
    name="clean",
    description="Clean messages in this channel (filters: all, bots, user)",
)
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.describe(
    filter="Which messages to remove: all | bots | user",
    limit="How many messages to scan (max 1000)",
    user="When filter=user, target this member",
)
async def clean_cmd(
    interaction: discord.Interaction,
    filter: str = "all",  # noqa: A002
    limit: int = 100,
    user: discord.Member | None = None,
) -> None:
    """Handles the /clean command."""
    await Cleaner.clean_channel(interaction, filter_value=filter, limit=limit, user=user)


@BOT.tree.command(
    name="archive-raid",
    description="Forward Raid-Helper embeds to another channel and delete originals",
)
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.describe(
    source="The channel to search for the Raid-Helper messages",
    destination="The channel to move the messages to",
    tag="Optional tag to look for inside the embed (e.g. #sun_gw)",
    archive_limit="Max matched messages to archive (default 50)",
    scan_limit="How many messages back to search overall (default 200)",
)
async def archive_raid_cmd(
    interaction: discord.Interaction,
    source: discord.TextChannel,
    destination: discord.TextChannel,
    tag: str | None = None,
    archive_limit: int = 50,
    scan_limit: int = 200,
) -> None:
    """Finds multiple Raid-Helper messages, forwards them, and deletes the originals."""
    log(
        f"[ARCHIVE] Initiated by @{interaction.user.display_name} | From: #{source.name} "
        f"-> To: #{destination.name} | Tag: {tag} | Max Archive: {archive_limit} "
        f"| Scan depth: {scan_limit}",
    )

    await interaction.response.defer(ephemeral=True)

    if not interaction.user.guild_permissions.manage_messages:
        log(f"[ARCHIVE] Failed: @{interaction.user.display_name} lacks Manage Messages perm.")
        await interaction.followup.send(
            "You need Manage Messages permission to use this.", ephemeral=True,
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
            f"Could not find any Raid-Helper messages{msg_suffix} in the last "
            f"{scan_limit} messages of {source.mention}.",
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

    log(f"[ARCHIVE] Complete: {archived_count} moved, {failed_count} failed.")

    fail_txt = f" ({failed_count} failed due to API errors)" if failed_count > 0 else ""
    await interaction.followup.send(
        f"**Archive Complete!**\n"
        f"Moved **{archived_count}** Raid-Helper messages to {destination.mention}."
        f"{fail_txt}",
        ephemeral=True,
    )


@BOT.tree.command(
    name="parse-roster",
    description="Extract Accepted and Maybe users from a Raid-Helper embed into a table",
)
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.describe(
    message_id="The ID of the Raid-Helper message",
    destination="The channel where the bot will post the generated tables",
)
async def parse_roster_cmd(
    interaction: discord.Interaction,
    message_id: str,
    destination: discord.TextChannel,
) -> None:
    """Parses a Raid-Helper message and creates a tabular summary sent to a channel."""
    log(
        f"[ROSTER] Initiated by @{interaction.user.display_name} for msg {message_id} "
        f"-> To: #{destination.name}",
    )

    await interaction.response.defer(ephemeral=True)

    source_channel = interaction.channel
    if not isinstance(source_channel, discord.TextChannel):
        await interaction.followup.send(
            "This command must be run in a text channel.", ephemeral=True,
        )
        return

    try:
        msg_id_int = int(message_id.strip())
        target_msg = await source_channel.fetch_message(msg_id_int)
    except ValueError:
        await interaction.followup.send("Message ID must be a valid number.", ephemeral=True)
        return
    except discord.NotFound:
        await interaction.followup.send(
            "Message not found in this channel. Check the ID.", ephemeral=True,
        )
        return
    except discord.HTTPException as e:
        await interaction.followup.send(f"API Error: {e}", ephemeral=True)
        return

    if target_msg.author.id != Config.RAID_HELPER_ID:
        await interaction.followup.send(
            "That message was not sent by the Raid-Helper bot.", ephemeral=True,
        )
        return

    raw_text_blocks = [target_msg.content]
    for embed in target_msg.embeds:
        if embed.title:
            raw_text_blocks.append(embed.title)
        if embed.description:
            raw_text_blocks.append(embed.description)
        for field in embed.fields:
            raw_text_blocks.append(field.name)
            raw_text_blocks.append(field.value)

    raw_text = "\n".join(filter(None, raw_text_blocks))

    # Clean text: remove custom emojis and markdown
    text_no_emojis = re.sub(r"<a?:\w+:\d+>", "", raw_text)
    text_cleaned = re.sub(r"[*_`~]", "", text_no_emojis)

    lines = text_cleaned.split("\n")
    accepted, maybe = [], []
    current_list = None

    strip_pattern = r"^[\s\u2000-\u200F\u2800\uFEFF\u00A0]+|[\s\u2000-\u200F\u2800\uFEFF\u00A0]+$"

    for line in lines:
        clean_line = re.sub(strip_pattern, "", line)
        if not clean_line:
            continue

        lower_line = clean_line.lower()

        if "accepted" in lower_line and len(lower_line) < 40:
            current_list = accepted
            continue
        if ("maybe" in lower_line or "tentative" in lower_line) and len(lower_line) < 40:
            current_list = maybe
            continue
        if (
            any(w in lower_line for w in ("declined", "absence", "late"))
            and len(lower_line) < 40
        ):
            current_list = None
            continue

        if current_list is not None:
            match = re.match(r"^\D*?(\d+)[.,:;\s\u00A0]+(.+)$", clean_line)
            if match:
                slot = match.group(1)
                name = match.group(2).strip()
                current_list.append((slot, name))

    if not accepted and not maybe:
        await interaction.followup.send(
            "Could not find any 'Accepted' or 'Maybe' users. "
            "The embed might be empty or formatted unusually.",
            ephemeral=True,
        )
        log(f"[ROSTER] Failed: Found no users for {message_id}. Regex bypassed.")
        return

    # Build the response using Discord Native Markdown Tables
    response_lines = []
    if accepted:
        response_lines.extend(["# ✅ Accepted", "| Slot | Name |", "|---|---|"])
        for role, name in accepted:
            response_lines.append(f"| {role} | {name} |")
        response_lines.append("")

    if maybe:
        response_lines.extend(["# ❔ Maybe", "| Slot | Name |", "|---|---|"])
        for role, name in maybe:
            response_lines.append(f"| {role} | {name} |")

    # Combine everything into a single string
    inner_text = "\n".join(response_lines)

    # Wrap it in a markdown codeblock
    final_message = f"```markdown\n{inner_text}\n```"

    # Ensure it doesn't break Discord's 2000 character limit
    # If it's too long, truncate the text INSIDE so we don't lose the closing backticks
    if len(final_message) > 2000:
        safe_inner = inner_text[:1980] + "..."
        final_message = f"```markdown\n{safe_inner}\n```"

    try:
        await destination.send(final_message)
        await interaction.followup.send(
            f"Successfully processed roster and sent to {destination.mention}!",
            ephemeral=True,
        )
        log(
            f"[ROSTER] Successfully parsed roster and sent to #{destination.name} "
            f"({len(accepted)} Accepted, {len(maybe)} Maybe)",
        )
    except discord.Forbidden:
        await interaction.followup.send(
            f"I don't have permission to send messages in {destination.mention}.",
            ephemeral=True,
        )
        log(f"[ROSTER] Failed: Lacking permissions to write in #{destination.name}")


@BOT.tree.command(
    name="list-members",
    description="List server usernames directly in chat (optional: filter by role)",
)
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.describe(
    role="Only list members who have this specific role",
)
async def list_members_cmd(
    interaction: discord.Interaction,
    role: discord.Role | None = None,
) -> None:
    """Lists server usernames directly in chat, handling Discord's character limit."""
    role_log = f" | Filter: @{role.name}" if role else ""
    log(f"[MEMBERS] Username list requested by @{interaction.user.display_name}{role_log}")

    await interaction.response.defer(ephemeral=True)

    if not interaction.guild:
        await interaction.followup.send(
            "This command must be run in a server.", ephemeral=True,
        )
        return

    # Filter members if a role is provided
    if role:
        members_to_list = [m for m in interaction.guild.members if role in m.roles]
        header = f"**Usernames with role {role.name} ({len(members_to_list)}):**\n"
    else:
        members_to_list = interaction.guild.members
        header = f"**All Server Usernames ({len(members_to_list)}):**\n"

    if not members_to_list:
        msg = f"No members found with the role `{role.name}`." if role else "No members."
        await interaction.followup.send(msg, ephemeral=True)
        return

    # Extract ONLY the raw usernames, sorted alphabetically
    sorted_members = sorted(members_to_list, key=lambda m: m.display_name.lower())

    message_chunks = []
    current_chunk = header

    for member in sorted_members:
        addition = f"{member.display_name}\n"

        # Discord limit is 2000. 1950 gives us a safe buffer.
        if len(current_chunk) + len(addition) > 1950:
            message_chunks.append(current_chunk)
            current_chunk = addition
        else:
            current_chunk += addition

    # Make sure we don't forget the last batch of names
    if current_chunk:
        message_chunks.append(current_chunk)

    # Send all chunks sequentially
    for chunk in message_chunks:
        await interaction.followup.send(chunk, ephemeral=True)

    log(f"[MEMBERS] Successfully sent {len(message_chunks)} messages with usernames.")


def main() -> int:
    """Load environment and run the bot."""
    token = os.getenv("MAGIC")
    if not token:
        print("Missing MAGIC token in environment variables.")
        return 1
    BOT.run(token, log_handler=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
