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
    description="Group Raid roster into Discord Embeds with side-by-side columns",
)
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.describe(
    raid_msg="Message ID or Link for the Raid-Helper signup",
    template_msg="Message ID or Link for the filled template table",
    destination="The channel where the bot will post the embeds",
)
async def parse_roster_cmd(
    interaction: discord.Interaction,
    raid_msg: str,
    template_msg: str,
    destination: discord.TextChannel,
) -> None:
    """Parses Raid roster and groups members into side-by-side Embed columns."""
    log(f"[ROSTER] Initiated by @{interaction.user.name} -> To: #{destination.name}")

    await interaction.response.defer(ephemeral=True)

    if not interaction.guild:
        await interaction.followup.send("Must be run in a server.", ephemeral=True)
        return

    # Helper function to smartly fetch messages using either a Link or an ID
    async def get_msg(input_str: str) -> discord.Message | None:
        input_str = input_str.strip()
        try:
            if "discord.com/channels/" in input_str:
                parts = input_str.split("/")
                ch_id, m_id = int(parts[-2]), int(parts[-1])
                channel = interaction.guild.get_channel(ch_id)
                if not channel:
                    channel = await interaction.guild.fetch_channel(ch_id)

                if isinstance(channel, discord.TextChannel):
                    return await channel.fetch_message(m_id)
                return None
            return await interaction.channel.fetch_message(int(input_str))
        except Exception:
            return None

    r_msg = await get_msg(raid_msg)
    t_msg = await get_msg(template_msg)

    if not r_msg or not t_msg:
        await interaction.followup.send(
            "Could not find one or both messages. Try pasting the full Message Link.",
            ephemeral=True,
        )
        return

    if r_msg.author.id != Config.RAID_HELPER_ID:
        await interaction.followup.send(
            "The Raid message was not sent by the Raid-Helper bot.", ephemeral=True,
        )
        return

    # 1. Parse the Template Message to map Nicknames -> Roles
    template_roles = {}
    for line in t_msg.content.split("\n"):
        line = line.strip()
        if line.startswith("|") and line.endswith("|"):
            parts = [p.strip() for p in line.split("|")[1:-1]]
            if len(parts) >= 2:
                nick, role = parts[0], parts[1]
                if nick.lower() == "nickname" or set(nick) == {"-"}:
                    continue
                template_roles[nick.lower()] = role if role else "Unassigned"

    if not template_roles:
        await interaction.followup.send(
            "Could not find a valid populated table in the template message.", ephemeral=True,
        )
        return

    # 2. Parse the Raid-Helper Message
    raw_text_blocks = [r_msg.content]
    for embed in r_msg.embeds:
        if embed.title:
            raw_text_blocks.append(embed.title)
        if embed.description:
            raw_text_blocks.append(embed.description)
        for field in embed.fields:
            raw_text_blocks.append(field.name)
            raw_text_blocks.append(field.value)

    raw_text = "\n".join(filter(None, raw_text_blocks))
    text_cleaned = re.sub(r"[*_`~<a?:\w+:\d+>]", "", raw_text)

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
                name = match.group(2).strip()
                current_list.append(name)

    if not accepted and not maybe:
        await interaction.followup.send(
            "Could not find any users in the Raid message.", ephemeral=True,
        )
        return

    # 3. Process Roles (Splits at first space or parenthesis)
    def process_role(name: str, raw_role: str) -> tuple[str, str]:
        if raw_role == "Unassigned" or not raw_role:
            return "Unassigned", name

        match = re.match(r"^([^\s\(]+)(.*)$", raw_role.strip())
        if match:
            category = match.group(1).strip()
            # Strip trailing/leading spaces and parens from the tag
            remainder = match.group(2).strip(" ()")

            if remainder:
                return category, f"{name} ({remainder})"
            return category, name
        return raw_role, name

    acc_groups = defaultdict(list)
    may_groups = defaultdict(list)

    for name in accepted:
        raw_role = template_roles.get(name.lower(), "Unassigned")
        cat, display_name = process_role(name, raw_role)
        acc_groups[cat].append(display_name)

    for name in maybe:
        raw_role = template_roles.get(name.lower(), "Unassigned")
        cat, display_name = process_role(name, raw_role)
        may_groups[cat].append(display_name)

    # 4. Build the final Embeds
    embeds = []

    if acc_groups:
        total_acc = sum(len(m) for m in acc_groups.values())
        em_acc = discord.Embed(title=f"✅ Accepted ({total_acc})", color=discord.Color.green())

        for role_cat in sorted(acc_groups.keys()):
            members = acc_groups[role_cat]
            val = "\n".join(f"- {m}" for m in members)
            # Prevent Discord's 1024-character field limit from crashing the bot
            if len(val) > 1024:
                val = val[:1020] + "..."
            em_acc.add_field(name=f"**{role_cat} ({len(members)})**", value=val, inline=True)

        embeds.append(em_acc)

    if may_groups:
        total_may = sum(len(m) for m in may_groups.values())
        em_may = discord.Embed(title=f"❔ Maybe ({total_may})", color=discord.Color.gold())

        for role_cat in sorted(may_groups.keys()):
            members = may_groups[role_cat]
            val = "\n".join(f"- {m}" for m in members)
            if len(val) > 1024:
                val = val[:1020] + "..."
            em_may.add_field(name=f"**{role_cat} ({len(members)})**", value=val, inline=True)

        embeds.append(em_may)

    try:
        # Send all generated embeds in a single beautiful message
        await destination.send(embeds=embeds)
        await interaction.followup.send(
            f"Successfully processed roster and sent to {destination.mention}!",
            ephemeral=True,
        )
        log(f"[ROSTER] Successfully parsed roster and sent to #{destination.name}")
    except discord.Forbidden:
        await interaction.followup.send(
            f"I lack permissions to send embeds in {destination.mention}.",
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
