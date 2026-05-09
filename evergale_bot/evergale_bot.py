"""Evergale BOT — optimized object oriented implementation (debounced live embeds)."""

import asyncio
import contextlib
import datetime
import os
import sys

import discord
from discord.ext import commands
from dotenv import load_dotenv

INTENTS = discord.Intents.default()
INTENTS.guilds = True
INTENTS.members = True
INTENTS.reactions = True

BOT = commands.Bot(command_prefix="!", intents=INTENTS)


class Config:
    """Static configuration values."""

    GUILD_ID: int = 1477751542705688828
    DEBOUNCE_SECONDS: float = 0.5
    MAX_RETRIES: int = 5
    DEFAULT_TIMEOUT: int = 30
    MAX_PURGE_SCAN: int = 1000


REACTION_ROLES: dict[str, dict[str, int | str]] = {
    "🗡️": {"name": "Bellstrike - Splendor", "capacity": 5},
    "⚔️": {"name": "Bellstrike - Umbra", "capacity": 5},
    "🛡️": {"name": "Stonesplit - Might", "capacity": 3},
    "⛱️": {"name": "Silkbind - Jade", "capacity": 6},
    "🌪️": {"name": "Bamboocut - Wind", "capacity": 6},
    "💧": {"name": "Silkbind - Deluge", "capacity": 4},
    "🌫️": {"name": "Bamboocut - Dust", "capacity": 4},
    "🔨": {"name": "Stonesplit - Strength", "capacity": 3},
    "🔥": {"name": "Bamboocut - Kite", "capacity": 4},
}


class Debouncer:
    """Debounce and coalesce frequent update requests per message."""

    def __init__(self) -> None:
        """INIT."""
        self._tasks: dict[int, asyncio.Task] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._debounce_seconds: float = Config.DEBOUNCE_SECONDS
        self._max_retries: int = Config.MAX_RETRIES

    def schedule(self, message_id: int, channel: discord.TextChannel) -> None:
        """Schedule a debounced update for a message id."""
        existing = self._tasks.get(message_id)
        if existing and not existing.done():
            existing.cancel()

        async def _delayed_update() -> None:
            try:
                await asyncio.sleep(self._debounce_seconds)
                try:
                    msg = await channel.fetch_message(message_id)
                except discord.NotFound:
                    ReactionRoleManager.untrack(message_id)
                    return
                await self._perform_update_with_backoff(msg)
            except asyncio.CancelledError:
                return
            finally:
                self._tasks.pop(message_id, None)

        task = asyncio.create_task(_delayed_update())
        self._tasks[message_id] = task

    async def _perform_update_with_backoff(self, msg: discord.Message) -> None:
        """Edit embed with retries and exponential backoff."""
        lock = self._locks.setdefault(msg.id, asyncio.Lock())
        async with lock:
            attempt = 0
            backoff = 0.5
            while attempt < self._max_retries:
                try:
                    end_time = ReactionRoleManager.end_times.get(msg.id)
                    embed = await ReactionRoleManager.build_status_embed(
                        msg,
                        organizer=msg.author if isinstance(msg.author, discord.Member) else None,
                        end_time_utc=end_time,
                    )
                    await msg.edit(embed=embed)
                    return
                except discord.NotFound:
                    ReactionRoleManager.untrack(msg.id)
                    return
                except discord.HTTPException:
                    attempt += 1
                    await asyncio.sleep(backoff)
                    backoff *= 2
            # final attempt, suppress exceptions
            with contextlib.suppress(Exception):
                end_time = ReactionRoleManager.end_times.get(msg.id)
                embed = await ReactionRoleManager.build_status_embed(
                    msg,
                    organizer=msg.author if isinstance(msg.author, discord.Member) else None,
                    end_time_utc=end_time,
                )
                await msg.edit(embed=embed)


class ReactionRoleManager:
    """Manage reaction-role embeds, tracking and embed generation."""

    tracked: dict[int, int] = {}
    end_times: dict[int, datetime.datetime] = {}
    debouncer = Debouncer()

    @classmethod
    async def build_status_embed(
        cls,
        message: discord.Message,
        organizer: discord.Member | None = None,
        end_time_utc: datetime.datetime | None = None,
    ) -> discord.Embed:
        """Construct the compact raid-style embed with timestamp and seconds."""
        embed = discord.Embed(
            title="TEST",
            description="Sign up to test new functions.",
            color=discord.Color.dark_teal(),
        )

        if organizer:
            embed.add_field(name="Organizer", value=organizer.display_name, inline=True)

        total_capacity = sum(
            info["capacity"]
            for info in REACTION_ROLES.values()
            if isinstance(info.get("capacity"), int)
        )
        embed.add_field(name="Capacity", value=f"{total_capacity} slots", inline=True)

        if end_time_utc:
            # embed.timestamp is rendered localized by clients
            embed.timestamp = end_time_utc
            # explicit seconds field using Discord timestamp formatting
            embed.add_field(
                name="Ends at",
                value=discord.utils.format_dt(end_time_utc, style="T"),
                inline=True,
            )

        embed.add_field(name="\u200b", value="\u200b", inline=False)

        for emoji, info in REACTION_ROLES.items():
            role_name: str = info.get("name", "Role")
            capacity: int | None = info.get("capacity")
            count = 0

            reaction_obj: discord.Reaction | None = None
            for r in message.reactions:
                if str(r.emoji) == emoji:
                    reaction_obj = r
                    break

            if reaction_obj:
                try:
                    users = [u async for u in reaction_obj.users(limit=None)]
                except discord.NotFound:
                    users = []
                except discord.HTTPException:
                    users = []
                participants = [u for u in users if not u.bot]
                count = len(participants)

            value = f"**{count} / {capacity}**" if capacity else f"**{count}**"
            embed.add_field(name=f"{emoji} {role_name}", value=value, inline=True)

        return embed

    @classmethod
    async def create_reactionroles(cls, interaction: discord.Interaction,
                                   timeout: int = Config.DEFAULT_TIMEOUT) -> None:
        """Slash command handler: post embed, add reactions, and track message."""
        await interaction.response.defer(ephemeral=True)

        now_utc = datetime.datetime.now(datetime.UTC)
        end_dt_utc = (now_utc + datetime.timedelta(seconds=timeout)).replace(microsecond=0)

        initial_embed = discord.Embed(
            title="TEST",
            description="Sign up to test new functions.",
            color=discord.Color.dark_teal(),
        )
        initial_embed.add_field(name="Organizer", value=interaction.user.display_name, inline=True)

        total_capacity = sum(info["capacity"] for info in REACTION_ROLES.values()
                             if isinstance(info.get("capacity"), int))
        initial_embed.add_field(name="Capacity", value=f"{total_capacity} slots", inline=True)

        initial_embed.timestamp = end_dt_utc
        initial_embed.add_field(name="Ends at",
                                value=discord.utils.format_dt(end_dt_utc, style="T"), inline=True)
        initial_embed.add_field(name="\u200b", value="\u200b", inline=False)

        for emoji, info in REACTION_ROLES.items():
            role_name: str = info.get("name", "Role")
            capacity: int | None = info.get("capacity")
            if capacity:
                initial_embed.add_field(name=f"{emoji} {role_name}", value=f"**0 / {capacity}**",
                                        inline=True)
            else:
                initial_embed.add_field(name=f"{emoji} {role_name}", value="**0**", inline=True)

        channel: discord.TextChannel = interaction.channel  # type: ignore[assignment]
        msg: discord.Message = await channel.send(embed=initial_embed)

        for emoji in REACTION_ROLES:
            with contextlib.suppress(discord.HTTPException):
                await msg.add_reaction(emoji)

        cls.tracked[msg.id] = msg.channel.id
        cls.end_times[msg.id] = end_dt_utc

        await interaction.followup.send("Reaction role embed posted and now live!", ephemeral=True)

        await asyncio.sleep(timeout)

        try:
            msg = await channel.fetch_message(msg.id)
        except discord.NotFound:
            cls.untrack(msg.id)
            await channel.send("The reaction role message was removed before the event ended.")
            return
        except discord.HTTPException:
            cls.untrack(msg.id)
            await channel.send("Could not fetch the reaction role message to build final stats.")
            return

        final_embed = discord.Embed(title="FINAL",
                                    description=f"Final signup status after {timeout} seconds:",
                                    color=discord.Color.green())
        final_embed.add_field(name="Organizer", value=interaction.user.display_name, inline=True)
        final_embed.add_field(name="Status", value="Closed", inline=True)
        final_embed.add_field(name="\u200b", value="\u200b", inline=False)

        for emoji, info in REACTION_ROLES.items():
            role_name: str = info.get("name", "Role")
            capacity: int | None = info.get("capacity")

            reaction_obj: discord.Reaction | None = None
            for r in msg.reactions:
                if str(r.emoji) == emoji:
                    reaction_obj = r
                    break

            if reaction_obj:
                try:
                    users = [u async for u in reaction_obj.users(limit=None)]
                except discord.NotFound:
                    users = []
                except discord.HTTPException:
                    users = []
                participants = [u for u in users if not u.bot]
                count = len(participants)
            else:
                count = 0

            value = f"**{count} / {capacity}**" if capacity else f"**{count}**"
            final_embed.add_field(name=f"{emoji} {role_name}", value=value, inline=True)

        final_embed.timestamp = cls.end_times.get(msg.id)
        if cls.end_times.get(msg.id):
            final_embed.add_field(
                name="Ended at",
                value=discord.utils.format_dt(cls.end_times[msg.id], style="T"),
                inline=True,
            )

        await channel.send(embed=final_embed)

        cls.untrack(msg.id)
        with contextlib.suppress(discord.HTTPException):
            await msg.delete()

    @classmethod
    def untrack(cls, message_id: int) -> None:
        """Stop tracking a message and remove stored end time."""
        cls.tracked.pop(message_id, None)
        cls.end_times.pop(message_id, None)


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

        bot_member = interaction.guild.me or interaction.guild.get_member(BOT.user.id)
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

        try:
            async for msg in channel.history(limit=limit):
                if not check(msg):
                    continue
                if msg.created_at <= cutoff:
                    remaining.append(msg)
                else:
                    with contextlib.suppress(Exception):
                        await msg.delete()
                        deleted_count += 1
        except Exception:
            await interaction.followup.send("Failed to scan channel history. "
                                            "Check bot permissions.", ephemeral=True)
            return

        for old_msg in remaining:
            with contextlib.suppress(Exception):
                await old_msg.delete()
                deleted_count += 1

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


@BOT.tree.command(name="reactionroles",
              description="Post a reaction role embed that updates live and gives a final summary")
async def reactionroles_cmd(interaction: discord.Interaction,
                            timeout: int = Config.DEFAULT_TIMEOUT) -> None:
    """TODO."""
    await ReactionRoleManager.create_reactionroles(interaction, timeout=timeout)


@BOT.tree.command(name="clean",
                  description="Clean messages in this channel (filters: all, bots, user)")
@discord.app_commands.describe(
    filter="Which messages to remove: all | bots | user",
    limit="How many messages to scan (max 1000)",
    user="When filter=user, target this member",
)
async def clean_cmd(interaction: discord.Interaction, target: str = "all", limit: int = 100,
                    user: discord.Member | None = None) -> None:
    """TODO."""
    await Cleaner.clean_channel(interaction, filter_value=target, limit=limit, user=user)


@BOT.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
    """Schedule a debounced update when a reaction is added."""
    if payload.user_id == BOT.user.id:
        return
    if payload.message_id not in ReactionRoleManager.tracked:
        return

    guild = BOT.get_guild(payload.guild_id)
    if guild is None:
        return
    channel = guild.get_channel(payload.channel_id)
    if channel is None or not isinstance(channel, discord.TextChannel):
        return

    ReactionRoleManager.debouncer.schedule(payload.message_id, channel)


@BOT.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent) -> None:
    """Schedule a debounced update when a reaction is removed."""
    if payload.message_id not in ReactionRoleManager.tracked:
        return

    guild = BOT.get_guild(payload.guild_id)
    if guild is None:
        return
    channel = guild.get_channel(payload.channel_id)
    if channel is None or not isinstance(channel, discord.TextChannel):
        return

    ReactionRoleManager.debouncer.schedule(payload.message_id, channel)


def main() -> int:
    """Load environment and run the bot."""
    load_dotenv()
    token = os.getenv("MAGIC")
    if not token:
        return 1
    BOT.run(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
