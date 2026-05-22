"""Evergale BOT — utilities for cleaning channels and archiving Raid-Helper events."""

import asyncio
import datetime
import os
import re
import sys
from collections import defaultdict
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
    description="Group Raid-Helper roster by roles defined in a template message",
)
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.describe(
    raid_msg_id="The ID of the Raid-Helper message",
    template_msg_id="The ID of the filled template table message",
    destination="The channel where the bot will post the grouped tables",
)
async def parse_roster_cmd(
    interaction: discord.Interaction,
    raid_msg_id: str,
    template_msg_id: str,
    destination: discord.TextChannel,
) -> None:
    """Parses Raid roster and groups members by role using a reference table."""
    log(
        f"[ROSTER] Initiated by @{interaction.user.name} for Raid: {raid_msg_id} "
        f"| Template: {template_msg_id} -> To: #{destination.name}",
    )

    await interaction.response.defer(ephemeral=True)

    source_channel = interaction.channel
    if not isinstance(source_channel, discord.TextChannel):
        await interaction.followup.send(
            "This command must be run in a text channel.", ephemeral=True,
        )
        return

    # Fetch both messages
    try:
        r_id = int(raid_msg_id.strip())
        t_id = int(template_msg_id.strip())
        raid_msg = await source_channel.fetch_message(r_id)
        template_msg = await source_channel.fetch_message(t_id)
    except ValueError:
        await interaction.followup.send("Message IDs must be valid numbers.", ephemeral=True)
        return
    except discord.NotFound:
        await interaction.followup.send(
            "One or both messages not found in this channel.", ephemeral=True,
        )
        return
    except discord.HTTPException as e:
        await interaction.followup.send(f"API Error: {e}", ephemeral=True)
        return

    if raid_msg.author.id != Config.RAID_HELPER_ID:
        await interaction.followup.send(
            "The Raid message ID was not sent by the Raid-Helper bot.", ephemeral=True,
        )
        return

    # 1. Parse the Template Message to map Nicknames -> Roles
    template_roles = {}
    for line in template_msg.content.split("\n"):
        line = line.strip()
        # Look for markdown table rows
        if line.startswith("|") and line.endswith("|"):
            parts = [p.strip() for p in line.split("|")[1:-1]]
            if len(parts) >= 2:
                nick, role = parts[0], parts[1]
                # Skip markdown header text and structural dividers
                if nick.lower() == "nickname" or set(nick) == {"-"}:
                    continue
                # If role column was left blank, assign to "Unassigned"
                template_roles[nick.lower()] = role if role else "Unassigned"

    if not template_roles:
        await interaction.followup.send(
            "Could not find a valid populated table in the template message.", ephemeral=True,
        )
        return

    # 2. Parse the Raid-Helper Message
    raw_text_blocks = [raid_msg.content]
    for embed in raid_msg.embeds:
        if embed.title:
            raw_text_blocks.append(embed.title)
        if embed.description:
            raw_text_blocks.append(embed.description)
        for field in embed.fields:
            raw_text_blocks.append(field.name)
            raw_text_blocks.append(field.value)

    raw_text = "\n".join(filter(None, raw_text_blocks))
    text_no_emojis = re.sub(r"<a?:\w+:\d+>", "", raw_text)
    text_cleaned = re.sub(r"[*_`~]", "", text_no_emojis)

    lines = text_cleaned.split("\n")
    accepted, maybe = [], []
    current_list = None
    strip_pat = r"^[\s\u2000-\u200F\u2800\uFEFF\u00A0]+|[\s\u2000-\u200F\u2800\uFEFF\u00A0]+$"

    for line in lines:
        clean_line = re.sub(strip_pat, "", line)
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
            "Could not find any users in the Raid message.", ephemeral=True,
        )
        return

    # 3. Group the matched members by their Template Roles
    acc_groups = defaultdict(list)
    may_groups = defaultdict(list)

    for slot, name in accepted:
        # Cross-reference the dictionary (case-insensitive). Default to Unassigned.
        role = template_roles.get(name.lower(), "Unassigned")
        acc_groups[role].append((slot, name))

    for slot, name in maybe:
        role = template_roles.get(name.lower(), "Unassigned")
        may_groups[role].append((slot, name))

    # 4. Build the final grouped Markdown Tables
    response_lines = []

    if acc_groups:
        response_lines.append("# ✅ Accepted\n")
        # Sort roles alphabetically so the output is consistent
        for role in sorted(acc_groups.keys()):
            response_lines.append(f"### {role}")
            response_lines.extend(["| Slot | Name |", "|---|---|"])
            for slot, name in acc_groups[role]:
                response_lines.append(f"| {slot} | {name} |")
            response_lines.append("")

    if may_groups:
        response_lines.append("# ❔ Maybe\n")
        for role in sorted(may_groups.keys()):
            response_lines.append(f"### {role}")
            response_lines.extend(["| Slot | Name |", "|---|---|"])
            for slot, name in may_groups[role]:
                response_lines.append(f"| {slot} | {name} |")
            response_lines.append("")

    inner_text = "\n".join(response_lines)
    final_message = f"```markdown\n{inner_text}\n```"

    if len(final_message) > 2000:
        safe_inner = inner_text[:1980] + "..."
        final_message = f"```markdown\n{safe_inner}\n```"

    try:
        await destination.send(final_message)
        await interaction.followup.send(
            f"Successfully processed roster and sent to {destination.mention}!",
            ephemeral=True,
        )
        log(f"[ROSTER] Successfully parsed roster and sent to #{destination.name}")
    except discord.Forbidden:
        await interaction.followup.send(
            f"I lack permissions to send messages in {destination.mention}.",
            ephemeral=True,
        )
        log(f"[ROSTER] Failed: Lacking permissions to write in #{destination.name}")

@BOT.tree.command(
    name="list-members",
    description="List server nicknames in a blank table (optional: filter by role)",
)
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.describe(
    role="Only list members who have this specific role",
)
async def list_members_cmd(
    interaction: discord.Interaction,
    role: discord.Role | None = None,
) -> None:
    """Lists server nicknames in an ephemeral markdown table with a blank Role column."""
    role_log = f" | Filter: @{role.name}" if role else ""
    log(f"[MEMBERS] Blank table requested by @{interaction.user.name}{role_log}")

    await interaction.response.defer(ephemeral=True)

    if not interaction.guild:
        await interaction.followup.send(
            "This command must be run in a server.", ephemeral=True,
        )
        return

    # Filter members if a role is provided
    if role:
        members = [m for m in interaction.guild.members if role in m.roles]
        header = f"### Nicknames with role {role.name} ({len(members)})"
    else:
        members = interaction.guild.members
        header = f"### All Server Nicknames ({len(members)})"

    if not members:
        msg = f"No members found with the role `{role.name}`." if role else "No members."
        await interaction.followup.send(msg, ephemeral=True)
        return

    # Sort alphabetically by display name
    sorted_members = sorted(members, key=lambda m: m.display_name.lower())

    # Step 1: Pre-process data and find the maximum column width for nicknames
    parsed_names = []
    max_nick_len = len("Nickname")
    role_col_width = len("Role")  # Matches exactly to the word "Role"

    for member in sorted_members:
        clean_name = member.display_name.replace("|", "\\|")
        max_nick_len = max(max_nick_len, len(clean_name))
        parsed_names.append(clean_name)

    # Step 2: Build dynamic headers using the calculated widths
    table_header = f"| {'Nickname'.ljust(max_nick_len)} | {'Role'.ljust(role_col_width)} |\n"
    table_divider = f"|{'-' * (max_nick_len + 2)}|{'-' * (role_col_width + 2)}|\n"

    base_table = table_header + table_divider
    message_chunks = []
    current_inner = base_table

    # Step 3: Build the rows using precise spacing padding and an empty role column
    for name in parsed_names:
        addition = f"| {name.ljust(max_nick_len)} | {' ' * role_col_width} |\n"

        # Discord limit is 2000. 1900 gives a safe buffer.
        if len(current_inner) + len(addition) > 1900:
            message_chunks.append(f"{header}\n```markdown\n{current_inner}```")
            current_inner = base_table + addition
        else:
            current_inner += addition

    # Append the final leftover chunk
    if current_inner != base_table:
        message_chunks.append(f"{header}\n```markdown\n{current_inner}```")

    # Send all chunks sequentially
    for chunk in message_chunks:
        await interaction.followup.send(chunk, ephemeral=True)

    log(f"[MEMBERS] Sent {len(message_chunks)} blank table messages to @{interaction.user.name}.")

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
