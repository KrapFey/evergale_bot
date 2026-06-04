"""Evergale BOT — utilities for cleaning channels, archiving, and roster management."""

import asyncio
import datetime
import os
import random
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
    """Log a formatted message with a timestamp to the console and local log file."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{now}] 🤖 {message}"
    print(formatted_msg)
    with Path("app.log").open("a", encoding="utf-8") as log_file:
        log_file.write(formatted_msg + "\n")

class Config:
    """Static configuration values for the bot."""

    GUILD_ID: int = int(os.getenv("GUILD_ID", 0))
    MAX_PURGE_SCAN: int = 1000
    RAID_HELPER_ID: int = 579155972115660803

# ==========================================================
# UI COMPONENTS
# ==========================================================

class RosterSelect(discord.ui.Select):
    """Dropdown menu for selecting roster members."""

    def __init__(self, options: list[discord.SelectOption], placeholder: str) -> None:
        """Initialize the multi-select dropdown."""
        super().__init__(placeholder=placeholder, min_values=0, max_values=len(options),
                        options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle selection silently."""
        await interaction.response.defer()

class GroupSelectView(discord.ui.View):
    """Interactive view for roster selection."""

    def __init__(self, accepted: list[str], maybe: list[str],
                 destination: discord.TextChannel) -> None:
        """Initialize view with separated dropdowns."""
        super().__init__(timeout=600)
        self.accepted = accepted
        self.maybe = maybe
        self.destination = destination
        self.selects = []
        if accepted:
            acc_chunks = [accepted[i : i + 25] for i in range(0, len(accepted), 25)]
            for i, chunk in enumerate(acc_chunks):
                options = [discord.SelectOption(label=name) for name in chunk]
                placeholder = f"✅ Group A (Accepted - Part {i+1})..."
                select = RosterSelect(options, placeholder=placeholder)
                self.selects.append(select)
                self.add_item(select)
        if maybe:
            may_chunks = [maybe[i : i + 25] for i in range(0, len(maybe), 25)]
            for i, chunk in enumerate(may_chunks):
                options = [discord.SelectOption(label=name) for name in chunk]
                placeholder = f"❔ Group A (Maybe - Part {i+1})..."
                select = RosterSelect(options, placeholder=placeholder)
                self.selects.append(select)
                self.add_item(select)

    @discord.ui.button(label="Confirm & Generate", style=discord.ButtonStyle.green, row=4)
    async def confirm_btn(self, interaction: discord.Interaction,
                          _button: discord.ui.Button) -> None:
        """Generate final color-coded report."""
        def get_icon(cat: str) -> str:
            return "🔴" if cat == "A" else "🟢" if cat == "D" else "🟠"

        await interaction.response.defer(ephemeral=True)
        group_a_users = {user for select in self.selects for user in select.values}
        acc_groups, may_groups = defaultdict(list), defaultdict(list)
        for name in self.accepted:
            acc_groups["A" if name in group_a_users else "D"].append(name)
        for name in self.maybe:
            may_groups["A" if name in group_a_users else "D"].append(name)
        embeds = []
        pad, stretcher = "\u2800" * 12, "\u2800" * 60
        for groups, title, color in [(acc_groups, "Accepted", discord.Color.green()),
                                     (may_groups, "Maybe", discord.Color.gold())]:
            if groups:
                em = discord.Embed(title=f"{title} ({sum(len(m) for m in groups.values())})",
                                   color=color)
                for cat in sorted(groups.keys()):
                    sorted_m = sorted(groups[cat], key=lambda m: m.lower())
                    val = "\n".join(f"- {m}" for m in sorted_m)
                    em.add_field(name=f"{get_icon(cat)} **{cat} ({len(sorted_m)})** {pad}",
                                 value=val[:1020] + "..." if len(val) > 1024 else val, inline=True)
                em.set_footer(text=stretcher)
                embeds.append(em)
        try:
            await self.destination.send(embeds=embeds)
            await interaction.followup.send("Report sent!", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("Lacking permissions.", ephemeral=True)
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)
        self.stop()

# ==========================================================
# COMMAND GROUPS
# ==========================================================

boss = discord.app_commands.Group(name="boss", description="Boss database management")
roster = discord.app_commands.Group(name="roster", description="Raid roster management")
utility = discord.app_commands.Group(name="utility", description="General utility commands")

@boss.command(name="list", description="List all bosses")
async def boss_list(interaction: discord.Interaction) -> None:
    """Display ordered boss list."""
    log(f"[BOSSES] List requested by @{interaction.user.display_name}")
    await interaction.response.defer(ephemeral=True)
    file_path = Path("bosses.txt")
    if not file_path.exists():
        await interaction.followup.send("Boss list is not available yet.", ephemeral=True)
        return
    try:
        with file_path.open("r", encoding="utf-8") as f:
            bosses = [line.strip() for line in f if line.strip()]
    except Exception as e:
        log(f"[BOSSES] Error reading file: {e}")
        await interaction.followup.send("An error occurred while reading the Boss list.",
                                        ephemeral=True)
        return
    if not bosses:
        await interaction.followup.send("The Boss list is currently empty.", ephemeral=True)
        return
    response_lines = ["### 🐉 Boss List"]
    for i, boss in enumerate(bosses, start=1):
        response_lines.append(f"{i}. {boss}")
    chunk = ""
    for line in response_lines:
        if len(chunk) + len(line) + 1 > 1900:
            await interaction.followup.send(chunk, ephemeral=True)
            chunk = line + "\n"
        else:
            chunk += line + "\n"
    if chunk:
        await interaction.followup.send(chunk, ephemeral=True)

@boss.command(name="add", description="Add a boss")
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.describe(name="The name of the boss to add (can contain spaces)")
async def boss_add(interaction: discord.Interaction, name: str) -> None:
    """Add boss to list."""
    boss_name = name.strip()
    log(f"[BOSSES] Add requested by @{interaction.user.display_name} -> {boss_name}")
    await interaction.response.defer(ephemeral=True)
    if not boss_name:
        await interaction.followup.send("Boss name cannot be empty.", ephemeral=True)
        return
    file_path = Path("bosses.txt")
    try:
        with file_path.open("a", encoding="utf-8") as f:
            f.write(f"{boss_name}\n")
    except Exception as e:
        log(f"[BOSSES] Error writing to file: {e}")
        await interaction.followup.send("An error occurred while saving the boss to the file.",
                                        ephemeral=True)
        return
    await interaction.followup.send(f"Successfully added **{boss_name}** to the boss list!",
                                    ephemeral=True)

@boss.command(name="remove", description="Remove a boss")
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.describe(identifier="The exact name or the list number of the boss to remove")
async def boss_remove(interaction: discord.Interaction, identifier: str) -> None:
    """Remove boss by name or index."""
    identifier = identifier.strip()
    log(f"[BOSSES] Remove requested by @{interaction.user.display_name} -> {identifier}")
    await interaction.response.defer(ephemeral=True)
    file_path = Path("bosses.txt")
    if not file_path.exists():
        await interaction.followup.send("The `bosses.txt` file does not exist yet. "
                                        "Nothing to remove.", ephemeral=True)
        return
    try:
        with file_path.open("r", encoding="utf-8") as f:
            bosses = [line.strip() for line in f if line.strip()]
    except Exception as e:
        log(f"[BOSSES] Error reading file: {e}")
        await interaction.followup.send("An error occurred while reading the `bosses.txt` file.",
                                        ephemeral=True)
        return
    if not bosses:
        await interaction.followup.send("The boss list is already empty.", ephemeral=True)
        return
    target_index = -1
    removed_boss_name = ""
    if identifier.isdigit():
        idx = int(identifier)
        if 1 <= idx <= len(bosses):
            target_index = idx - 1
    else:
        lower_ident = identifier.lower()
        for i, boss in enumerate(bosses):
            if boss.lower() == lower_ident:
                target_index = i
                break
    if target_index == -1:
        await interaction.followup.send(f"Could not find a boss matching **{identifier}**. "
                                        "Check the spelling or list number using `/list-bosses`.",
                                        ephemeral=True)
        return
    removed_boss_name = bosses.pop(target_index)
    try:
        with file_path.open("w", encoding="utf-8") as f:
            for boss in bosses:
                f.write(f"{boss}\n")
    except Exception as e:
        log(f"[BOSSES] Error writing to file: {e}")
        await interaction.followup.send("An error occurred while saving the updated list.",
                                        ephemeral=True)
        return
    await interaction.followup.send(f"Successfully removed **{removed_boss_name}** "
                                    "from the boss list!", ephemeral=True)

@boss.command(name="random", description="Get a random boss")
async def boss_random(interaction: discord.Interaction) -> None:
    """Pick random boss."""
    log(f"[BOSSES] Random requested by @{interaction.user.display_name}")
    await interaction.response.defer()
    file_path = Path("bosses.txt")
    if not file_path.exists():
        await interaction.followup.send("The `bosses.txt` file does not exist yet. "
                                        "Add some using `/add-boss`.", ephemeral=True)
        return
    try:
        with file_path.open("r", encoding="utf-8") as f:
            bosses = [line.strip() for line in f if line.strip()]
    except Exception as e:
        log(f"[BOSSES] Error reading file: {e}")
        await interaction.followup.send("An error occurred while reading the `bosses.txt` file.",
                                        ephemeral=True)
        return
    if not bosses:
        await interaction.followup.send("The boss list is currently empty.", ephemeral=True)
        return
    selected_boss = random.choice(bosses)
    await interaction.followup.send(f"🎲 The randomly selected boss is: **{selected_boss}**!")

@roster.command(name="generate", description="Generate report")
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.describe(raid_msg="Message ID or Link for the Raid-Helper signup",
                               destination="The channel where the bot will post the embeds")
async def roster_generate(interaction: discord.Interaction, raid_msg: str,
                          destination: discord.TextChannel) -> None:
    """Parse roster and send interactive view."""
    async def get_msg(input_str: str) -> discord.Message | None:
        """Fetch a message dynamically using either a URL or an ID string."""
        input_str = input_str.strip()
        try:
            if "discord.com/channels/" in input_str:
                parts = input_str.split("/")
                ch_id, m_id = int(parts[-2]), int(parts[-1])
                channel = interaction.guild.get_channel(ch_id)
                if not channel:
                    channel = await interaction.guild.fetch_channel(ch_id)
                return await channel.fetch_message(m_id)
            return await interaction.channel.fetch_message(int(input_str))
        except Exception as e:
            log(f"[ROSTER] Error fetching message: {e}")
            return None

    log(f"[ROSTER] Initiated by @{interaction.user.display_name} -> To: #{destination.name}")
    await interaction.response.defer(ephemeral=True)
    if not interaction.guild:
        await interaction.followup.send("Must be run in a server.", ephemeral=True)
        return
    r_msg = await get_msg(raid_msg)
    if not r_msg:
        fail_msg = "Could not find the Raid message. Make sure the link or ID is correct."
        await interaction.followup.send(fail_msg, ephemeral=True)
        return
    if r_msg.author.id != Config.RAID_HELPER_ID:
        fail_msg = "The message provided was not sent by the Raid-Helper bot."
        await interaction.followup.send(fail_msg, ephemeral=True)
        return
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
        if any(w in lower_line for w in ("declined", "absence", "late")) and len(lower_line) < 40:
            current_list = None
            continue
        if current_list is not None:
            match = re.match(r"^\D*?(\d+)[.,:;\s\u00A0]+(.+)$", clean_line)
            if match:
                name = match.group(2).strip()
                current_list.append(name)
    if not accepted and not maybe:
        await interaction.followup.send("Could not find any users in the message.", ephemeral=True)
        return
    accepted.sort(key=lambda m: m.lower())
    maybe.sort(key=lambda m: m.lower())
    view = GroupSelectView(accepted, maybe, destination)
    prompt = ("**Roster Setup:** Please select the players below who belong in **Group A**.\n"
            "*(Everyone else will automatically be placed in **Group D** when you click Confirm)*.")
    await interaction.followup.send(prompt, view=view, ephemeral=True)

@utility.command(name="clean", description="Clean channel")
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.describe(filter_value="Which messages to remove: all | bots | user",
                               limit="How many messages to scan (max 1000)",
                               user="When filter_val=user, target this member")
async def util_clean(interaction: discord.Interaction, filter_value: str = "all",
                     limit: int = 100, user: discord.Member | None = None) -> None:
    """Clean channel logic."""
    channel = getattr(interaction, "channel", None)
    channel_name = getattr(channel, "name", "unknown-channel")
    log(f"[CLEAN] Initiated by @{interaction.user.display_name} in #{channel_name} "
        f"(Filter: {filter_value}, Limit: {limit}, "
        f"User: {getattr(user, 'display_name', 'None')})")
    if not interaction.user.guild_permissions.manage_messages:
        log(f"[CLEAN] Failed: @{interaction.user.display_name} lacks Manage Messages.")
        await interaction.response.send_message("You need Manage Messages permission to use this.",
                                                ephemeral=True)
        return
    bot_member = interaction.guild.me
    if not bot_member or not bot_member.guild_permissions.manage_messages:
        log("[CLEAN] Failed: Bot lacks Manage Messages permission.")
        await interaction.response.send_message("I need Manage Messages permission to delete "
                                                "messages.", ephemeral=True)
        return
    filter_value = filter_value.lower()
    if filter_value not in ("all", "bots", "user"):
        await interaction.response.send_message("Invalid filter. Use `all`, `bots`, or `user`.",
                                                ephemeral=True)
        return
    limit = max(1, min(limit, Config.MAX_PURGE_SCAN))
    await interaction.response.defer(ephemeral=True)
    def check(msg: discord.Message) -> bool:
        """Determine if a scanned message meets the deletion criteria."""
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
        await interaction.followup.send("I don't have permission to delete messages in this "
                                        "channel.", ephemeral=True)
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
        await interaction.followup.send("Failed to scan channel history. Check bot permissions.",
                                        ephemeral=True)
        return
    tasks.extend([old_msg.delete() for old_msg in remaining])
    if tasks:
        log(f"[CLEAN] Concurrently deleting {len(tasks)} manual messages...")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        deleted_count += sum(1 for r in results if not isinstance(r, Exception))
    log(f"[CLEAN] Success: Removed {deleted_count} messages.")
    await interaction.followup.send(f"Clean complete — removed **{deleted_count}** msgs "
                                    f"(filter: **{filter_value}**).", ephemeral=True)

@utility.command(name="archive", description="Archive raids")
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.describe(source="The channel to search for the Raid-Helper messages",
                               destination="The channel to move the messages to",
                               tag="Optional tag to look for inside the embed (e.g. #sun_gw)",
                               archive_limit="Max matched messages to archive (default 50)",
                               scan_limit="How many messages back to search overall (default 200)")
async def util_archive(interaction: discord.Interaction, source: discord.TextChannel,
                       destination: discord.TextChannel, tag: str | None = None,
                       archive_limit: int = 50, scan_limit: int = 200) -> None:
    """Archive raid logic."""
    log(f"[ARCHIVE] Initiated by @{interaction.user.display_name} | From: #{source.name} "
        f"-> To: #{destination.name} | Tag: {tag} | Max Archive: {archive_limit} "
        f"| Scan depth: {scan_limit}")
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.guild_permissions.manage_messages:
        log(f"[ARCHIVE] Failed: @{interaction.user.display_name} lacks Manage Messages perm.")
        await interaction.followup.send("You need Manage Messages permission to use this.",
                                        ephemeral=True)
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
        await interaction.followup.send("I don't have permission to read message history "
                                        f"in {source.mention}.", ephemeral=True)
        return
    if not target_messages:
        log("[ARCHIVE] Success/Empty: No matching Raid-Helper messages found.")
        msg_suffix = f" containing the tag `{tag}`" if tag else ""
        await interaction.followup.send(f"Could not find any Raid-Helper messages{msg_suffix} in "
                                        f"the last {scan_limit} messages of {source.mention}.",
                                        ephemeral=True)
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
    await interaction.followup.send(f"**Archive Complete!**\n"
                                    f"Moved **{archived_count}** Raid-Helper messages "
                                    f"to {destination.mention}. {fail_txt}", ephemeral=True)

# Add groups to tree
BOT.tree.add_command(boss)
BOT.tree.add_command(roster)
BOT.tree.add_command(utility)

@BOT.event
async def on_ready() -> None:
    """Sync commands."""
    log(f"Logged in as {BOT.user.display_name} (ID: {BOT.user.id})")
    guild = discord.Object(id=Config.GUILD_ID)
    BOT.tree.copy_global_to(guild=guild)
    synced = await BOT.tree.sync(guild=guild)
    BOT.tree.clear_commands(guild=None)
    await BOT.tree.sync(guild=None)
    log(f"Cleared global cache and synced {len(synced)} commands to guild {Config.GUILD_ID}")

def main() -> int:
    """Entry point."""
    token = os.getenv("MAGIC")
    if not token:
        return 1
    BOT.run(token, log_handler=None)
    return 0

if __name__ == "__main__":
    sys.exit(main())
