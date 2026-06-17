"""Roster command group, UI components, and raid parser for Evergale BOT."""

import json
import re
from collections import defaultdict
from pathlib import Path

import discord
from discord import app_commands

from evergale_bot.src.config import ROLE_EMOJI_IDS, Config, get_role_emoji
from evergale_bot.src.logger import log
from evergale_bot.src.utils import parse_utc_date

_STRIP_PAT: str = r"^[\s -‏⠀﻿ ]+|[\s -‏⠀﻿ ]+$"
_NAME_PAT: re.Pattern[str] = re.compile(r"^\D*?(\d+)[.,:;\s ]+(.+)$")


class RaidParser:
    """Unified parser for extracting signup data from Raid-Helper messages."""

    @staticmethod
    def parse(msg: discord.Message) -> dict[str, int | dict[str, list[str]]]:
        """Parse a Raid-Helper message and return timestamp and user groups.

        Args:
            msg: The Raid-Helper Discord message to parse.

        Returns:
            A dict with keys ``timestamp`` (int) and ``groups``
            (dict with Accepted/Maybe/Declined lists).
        """
        raw_blocks = [msg.content]
        for embed in msg.embeds:
            if embed.title:
                raw_blocks.append(embed.title)
            if embed.description:
                raw_blocks.append(embed.description)
            for field in embed.fields:
                raw_blocks.append(field.name)
                raw_blocks.append(field.value)
        raw_text = "\n".join(filter(None, raw_blocks))
        ts_match = re.search(r"<t:(\d+)(?::[a-zA-Z])?>", raw_text)
        timestamp = int(ts_match.group(1)) if ts_match else 0
        text = re.sub(r"[*_`~]", "", re.sub(r"<a?:\w+:\d+>", "", raw_text))
        data: dict[str, list[str]] = {"Accepted": [], "Maybe": [], "Declined": []}
        current_list: list[str] | None = None
        for line in text.split("\n"):
            clean = re.sub(_STRIP_PAT, "", line)
            if not clean:
                continue
            lower = clean.lower()
            if "accepted" in lower and len(lower) < 40:
                current_list = data["Accepted"]
            elif ("maybe" in lower or "tentative" in lower) and len(lower) < 40:
                current_list = data["Maybe"]
            elif any(w in lower for w in ("declined", "absence", "late")) and len(lower) < 40:
                current_list = data["Declined"]
            elif current_list is not None:
                m = _NAME_PAT.match(clean)
                if m:
                    current_list.append(m.group(2).strip())
        return {"timestamp": timestamp, "groups": data}


class RosterSelect(discord.ui.Select):
    """Dropdown for selecting roster members."""

    def __init__(self, options: list[discord.SelectOption], placeholder: str) -> None:
        """Initialize the multi-select dropdown.

        Args:
            options: The selectable options.
            placeholder: Placeholder text shown when nothing is selected.
        """
        super().__init__(placeholder=placeholder, min_values=0, max_values=len(options),
                         options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Handle selection silently.

        Args:
            interaction: The Discord interaction context.
        """
        await interaction.response.defer()


class GroupSelectView(discord.ui.View):
    """Interactive view for Attack/Defense roster classification."""

    def __init__(self, accepted_data: list[tuple[str, discord.Member | None]],
                       maybe_data: list[tuple[str, discord.Member | None]],
                       destination: discord.TextChannel) -> None:
        """Initialize view with dropdowns and send target.

        Args:
            accepted_data: List of (name, member) tuples from Accepted.
            maybe_data: List of (name, member) tuples from Maybe.
            destination: Channel where the final report will be sent.
        """
        super().__init__(timeout=600)
        self.destination: discord.TextChannel = destination
        self.accepted_data: list[tuple[str, discord.Member | None]] = accepted_data
        self.maybe_data: list[tuple[str, discord.Member | None]] = maybe_data
        self.selects: list[RosterSelect] = []
        self.__add_chunks(accepted_data, "Attack (✅ Accepted)")
        self.__add_chunks(maybe_data, "Attack (❔ Maybe)")

    def __add_chunks(self, data: list[tuple[str, discord.Member | None]], prefix: str) -> None:
        """Split data into 25-item select chunks and add them to the view.

        Args:
            data: List of (name, member) tuples.
            prefix: Label prefix shown in the dropdown placeholder.
        """
        chunks = [data[i: i + 25] for i in range(0, len(data), 25)]
        for i, chunk in enumerate(chunks):
            options = [discord.SelectOption(label=name, value=name,
                                            emoji=get_role_emoji(member))
                       for name, member in chunk]
            select = RosterSelect(options, placeholder=f"{prefix} - Part {i + 1}...")
            self.selects.append(select)
            self.add_item(select)

    def __format_embeds(self, acc_groups: defaultdict[str, list[str]],
                        may_groups: defaultdict[str, list[str]],
                        emoji_lookup: dict[str, str],
                        icon: discord.PartialEmoji) -> list[discord.Embed]:
        """Render grouped data as Discord embeds.

        Args:
            acc_groups: Attack/Defense buckets for Accepted players.
            may_groups: Attack/Defense buckets for Maybe players.
            emoji_lookup: Maps player name to emoji string.
            icon: Fallback emoji.

        Returns:
            List of formatted embeds.
        """
        def get_cat_icon(cat: str) -> str:
            return "⚔️" if cat == "Attack" else "🛡️"

        pad, stretcher = "⠀" * 12, "⠀" * 60
        embeds = []
        for groups, title, color in [(acc_groups, "Accepted", discord.Color.green()),
                                     (may_groups, "Maybe", discord.Color.gold())]:
            if not groups:
                continue
            em = discord.Embed(title=f"{title} ({sum(len(m) for m in groups.values())})",
                               color=color)
            for cat in sorted(groups.keys()):
                sorted_m = sorted(groups[cat], key=lambda m: m.lower())
                lines = [f"{emoji_lookup.get(m, str(icon))} {m}" for m in sorted_m]
                val = "\n".join(lines)
                em.add_field(name=f"{get_cat_icon(cat)} **{cat} ({len(sorted_m)})** {pad}",
                             value=val[:1021] + "..." if len(val) > 1024 else val, inline=True)
            em.set_footer(text=stretcher)
            embeds.append(em)
        return embeds

    def __build_embeds(self, group_a: set[str]) -> list[discord.Embed]:
        """Build the color-coded Attack/Defense embeds.

        Args:
            group_a: Set of names assigned to Attack group.

        Returns:
            List of embeds (one per Accepted/Maybe section).
        """
        icon = discord.PartialEmoji(name="hybrid", id=ROLE_EMOJI_IDS["multi"])
        acc_groups: defaultdict[str, list[str]] = defaultdict(list)
        may_groups: defaultdict[str, list[str]] = defaultdict(list)
        emoji_lookup: dict[str, str] = {}
        for name, member in self.accepted_data:
            acc_groups["Attack" if name in group_a else "Defense"].append(name)
            emoji_obj = get_role_emoji(member)
            emoji_lookup[name] = str(emoji_obj) if emoji_obj else str(icon)
        for name, member in self.maybe_data:
            may_groups["Attack" if name in group_a else "Defense"].append(name)
            emoji_obj = get_role_emoji(member)
            emoji_lookup[name] = str(emoji_obj) if emoji_obj else str(icon)
        return self.__format_embeds(acc_groups, may_groups, emoji_lookup, icon)

    @discord.ui.button(label="Confirm & Generate", style=discord.ButtonStyle.green, row=4)
    async def confirm_btn(self, interaction: discord.Interaction,
                          _button: discord.ui.Button) -> None:
        """Generate and send the final color-coded report.

        Args:
            interaction: The Discord interaction context.
            _button: The button that was clicked (unused).
        """
        await interaction.response.defer(ephemeral=True)
        group_a = {user for select in self.selects for user in select.values}
        embeds = self.__build_embeds(group_a)
        try:
            await self.destination.send(embeds=embeds)
            await interaction.followup.send("Report sent!", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("I don't have permission to post in that channel.",
                                            ephemeral=True)
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)
        self.stop()


async def _fetch_message(interaction: discord.Interaction,
                         input_str: str) -> discord.Message | None:
    """Fetch a Discord message by URL or ID.

    Args:
        interaction: The Discord interaction (provides guild/channel context).
        input_str: A message URL or integer ID string.

    Returns:
        The fetched message, or None on failure.
    """
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
    except (discord.NotFound, discord.HTTPException, ValueError):
        log("[ROSTER] Message fetch failed")
        return None


def _resolve_members(names: list[str],
                     guild: discord.Guild) -> list[tuple[str, discord.Member | None]]:
    """Resolve a list of display names to guild members.

    Args:
        names: List of display name strings.
        guild: The Discord guild to search.

    Returns:
        List of (name, member_or_none) tuples.
    """
    return [(name, guild.get_member_named(name)) for name in names]


def _load_report_data(tag: str) -> dict[str, dict[str, list[str]]]:
    """Load attendance JSON data for the given tag.

    Args:
        tag: Event tag (e.g. ``<gvg_sat>`` or ``<gvg_all>``).

    Returns:
        Merged dict of timestamp -> groups data.
    """
    clean_tag = tag.replace("<", "").replace(">", "")
    if clean_tag not in Config.CLEAN_EVENT_TAGS | {"gvg_all"}:
        return {}
    files = ([Path("reports/gvg_sat.json"), Path("reports/gvg_sun.json")]
             if clean_tag == "gvg_all" else [Path(f"reports/{clean_tag}.json")])
    data: dict[str, dict[str, list[str]]] = {}
    for f in files:
        if f.exists():
            try:
                with f.open("r", encoding="utf-8") as fp:
                    data.update(json.load(fp))
            except json.JSONDecodeError:
                log(f"[ROSTER] Corrupted report file: {f.name}")
    return data


def _compute_stats(report_data: dict[str, dict[str, list[str]]],
                   start_ts: int, end_ts: int) -> tuple[
                       dict[str, dict[str, int]], int]:
    """Compute per-player attendance counts for the given time range.

    Args:
        report_data: Raw attendance data keyed by Unix timestamp string.
        start_ts: Inclusive start Unix timestamp.
        end_ts: Inclusive end Unix timestamp.

    Returns:
        Tuple of (stats dict, total event count).
    """
    stats: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {"Accepted": 0, "Maybe": 0, "Declined": 0})
    total = 0
    for ts_str, groups in report_data.items():
        try:
            ts = int(ts_str)
        except ValueError:
            continue
        if start_ts <= ts <= end_ts:
            total += 1
            for grp in ("Accepted", "Maybe", "Declined"):
                for player in groups.get(grp, []):
                    stats[player][grp] += 1
    return dict(stats), total


def _build_attendance_embed(tag: str, stats: dict[str, dict[str, int]],
                            total_events: int) -> discord.Embed:
    """Build the attendance leaderboard embed.

    Args:
        tag: Clean event tag label.
        stats: Per-player attendance counts.
        total_events: Total number of events in range.

    Returns:
        Formatted Discord embed.
    """
    embed = discord.Embed(title=f"📊 Attendance: {tag}", color=discord.Color.blue())
    player_list = sorted(stats.items(),
                         key=lambda x: x[1]["Accepted"] / total_events, reverse=True)
    pad_len = min(max((len(p) for p, _ in player_list), default=0), 20)
    desc = ""
    for player, counts in player_list:
        perc = (counts["Accepted"] / total_events) * 100
        p_name = player[:pad_len].ljust(pad_len)
        desc += (f"`{p_name}` A: `{counts['Accepted']:<2}` M: `{counts['Maybe']:<2}` "
                 f"D: `{counts['Declined']:<2}` %: `{perc:>5.1f}%`\n")
    embed.description = desc if len(desc) < 4096 else desc[:4090] + "..."
    embed.add_field(name="Total Events:", value=f"`{total_events}`", inline=False)
    embed.add_field(name="Legend:",
                    value="`A` - Accepted | `M` - Maybe | `D` - Declined | `%` - Attendance",
                    inline=False)
    return embed


class Roster(app_commands.Group, name="roster", description="Raid roster management"):
    """Slash command group for roster and attendance management."""

    @app_commands.command(name="generate", description="Generate report")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(raid_msg="Message ID or Link for the Raid-Helper signup",
                           destination="The channel where the bot will post the embeds")
    async def generate(self, interaction: discord.Interaction, raid_msg: str,
                              destination: discord.TextChannel) -> None:
        """Parse a Raid-Helper signup and launch the interactive roster view.

        Args:
            interaction: The Discord interaction context.
            raid_msg: Message URL or ID of the Raid-Helper signup post.
            destination: Channel to send the final roster report to.
        """
        name = interaction.user.display_name
        log(f"[ROSTER] Generate requested by @{name} -> #{destination.name}")
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild:
            await interaction.followup.send("Must be run in a server.", ephemeral=True)
            return
        r_msg = await _fetch_message(interaction, raid_msg)
        if not r_msg:
            await interaction.followup.send(
                "Could not find the Raid message. Make sure the link or ID is correct.",
                ephemeral=True)
            return
        if r_msg.author.id != Config.RAID_HELPER_ID:
            await interaction.followup.send(
                "The message provided was not sent by the Raid-Helper bot.", ephemeral=True)
            return
        parsed = RaidParser.parse(r_msg)
        groups = parsed["groups"]
        if not groups["Accepted"] and not groups["Maybe"]:
            await interaction.followup.send("Could not find any users in the message.",
                                            ephemeral=True)
            return
        resolved_accepted = _resolve_members(groups["Accepted"], interaction.guild)
        resolved_maybe = _resolve_members(groups["Maybe"], interaction.guild)
        view = GroupSelectView(resolved_accepted, resolved_maybe, destination)
        prompt = ("**Roster Setup:** Please select the players below who belong in "
                  "**Group Attack**.\n*(Everyone else will automatically be placed in "
                  "**Group Defense** when you click Confirm)*.")
        await interaction.followup.send(prompt, view=view, ephemeral=True)

    @app_commands.command(name="attendance", description="Generate an attendance report")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        tag="Which event tag to generate a report for",
        start_date="Start date (YYYY-MM-DD)",
        end_date="End date (YYYY-MM-DD)",
    )
    @app_commands.choices(tag=[
        app_commands.Choice(name="<gvg_all>", value="<gvg_all>"),
    ] + [app_commands.Choice(name=t, value=t) for t in Config.EVENT_TAGS])
    async def attendance(self, interaction: discord.Interaction, tag: str,
                                start_date: str | None = None,
                                end_date: str | None = None) -> None:
        """Generate an attendance leaderboard for the given event tag.

        Args:
            interaction: The Discord interaction context.
            tag: Event tag to filter by (or ``<gvg_all>``).
            start_date: Optional start date filter (YYYY-MM-DD, UTC).
            end_date: Optional end date filter (YYYY-MM-DD, UTC).
        """
        await interaction.response.defer()
        try:
            start_ts = parse_utc_date(start_date) if start_date else 0
            end_ts = parse_utc_date(end_date, end_of_day=True) if end_date else 9_999_999_999
        except ValueError:
            await interaction.followup.send("❌ Use `YYYY-MM-DD` format.", ephemeral=True)
            return
        report_data = _load_report_data(tag)
        if not report_data:
            await interaction.followup.send("No records found.", ephemeral=True)
            return
        stats, total_events = _compute_stats(report_data, start_ts, end_ts)
        if total_events == 0:
            await interaction.followup.send("No events in this range.", ephemeral=True)
            return
        clean_tag = tag.replace("<", "").replace(">", "")
        embed = _build_attendance_embed(clean_tag, stats, total_events)
        await interaction.followup.send(embed=embed)
